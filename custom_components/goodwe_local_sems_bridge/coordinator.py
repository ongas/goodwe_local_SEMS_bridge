"""SEMS relay coordinator for the GoodWe Local SEMS Bridge integration.

Reads inverter data via the goodwe library (read_runtime_data), constructs a
240-byte POSTGW plaintext from the decoded register values, encrypts it
(AES-128-CBC, key=0xFF×16), and sends the 294-byte packet to
tcp.goodwe-power.com:20001 over a persistent TCP connection.

Plaintext field offsets are empirically verified against captured SEMS packets
(see CONSOLIDATED_FIELD_VALIDATION.csv in the Inverter_MITM archive).
"""

from __future__ import annotations

import asyncio
import struct
from datetime import datetime, timezone
import logging
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from goodwe import InverterError, connect as goodwe_connect
from goodwe.inverter import Inverter

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


_LOGGER = logging.getLogger(__name__)

# Number of consecutive read_runtime_data failures before forcing a full
# goodwe_connect() reconnect.  Keeps the inverter object alive through
# transient UDP contention (e.g. official GoodWe integration polling at 500 ms).
_MAX_CONSECUTIVE_READ_FAILURES = 3

# ── POSTGW protocol constants ────────────────────────────────────────────────

SEMS_CLOUD_HOST = "tcp.goodwe-power.com"
SEMS_CLOUD_PORT = 20001

POSTGW_HEADER = b"POSTGW"
POSTGW_PACKET_TYPE = 0x0104
POSTGW_ENCRYPTION_KEY = bytes([0xFF] * 16)

# POSTGW plaintext layout (240 bytes = 15 AES blocks):
#   [0x00:0x15]  21 bytes  device header (firmware constant, captured at setup)
#   [0x15:0x1B]   6 bytes  timestamp (YY MM DD HH mm ss, local time)
#   [0x1B:0xEF] 213 bytes  mapped register fields (see CONSOLIDATED_FIELD_VALIDATION.csv)
POSTGW_PLAINTEXT_SIZE = 240
DEVICE_HEADER_SIZE = 21       # bytes 0x00–0x14: firmware constant, 21 bytes
TIMESTAMP_OFFSET = 0x15       # 6-byte timestamp inside plaintext
TIMESTAMP_SIZE = 6

# ── Plaintext field offsets ────────────────────────────────────────────────────
# The 240-byte plaintext is a direct sequential Modbus register dump.
# Byte 0x00-0x14 = 21-byte device header (firmware constant, captured at setup).
# Byte 0x15 onward = registers 30100-30172 at 2 bytes per register.
# Offset formula: PT_OFFSET = 0x15 + (REGISTER - 30100) * 2
#
# This was empirically verified: every known field (vpv1, vgrid1, pac, e_day,
# e_total, temperature, …) matches exactly when using this formula.

def _reg(register: int) -> int:
    """Plaintext byte offset for a Modbus holding register."""
    return 0x15 + (register - 30100) * 2

# Firmware sentinel bytes at plaintext offsets 0xCD–0xEF (beyond register range).
# Identical across all captured packets — required by SEMS for live display update.
_CONSTANT_TAIL = bytes.fromhex(
    "75fb75ff00000000000000000000"
    "76017602000000009121912200000000ffffffffff"
)
_CONSTANT_TAIL_OFFSET = 0xCD


def _crc16_modbus(data: bytes) -> int:
    """CRC-16 Modbus over the whole supplied buffer."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _aes_encrypt(plaintext: bytes, iv: bytes) -> bytes:
    cipher = Cipher(
        algorithms.AES(POSTGW_ENCRYPTION_KEY),
        modes.CBC(iv),
        backend=default_backend(),
    )
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()


def _build_postgw_packet(plaintext: bytes, device_id: str, device_serial: str) -> bytes:
    """Build a complete 294-byte POSTGW packet from a 240-byte plaintext."""
    assert len(plaintext) == POSTGW_PLAINTEXT_SIZE

    # IV = timestamp bytes from plaintext[0x15:0x1B] + 10 zeros
    ts_bytes = plaintext[TIMESTAMP_OFFSET : TIMESTAMP_OFFSET + TIMESTAMP_SIZE]
    iv = ts_bytes + bytes(10)

    ciphertext = _aes_encrypt(plaintext, iv)
    assert len(ciphertext) == POSTGW_PLAINTEXT_SIZE

    dev_id_bytes = device_id.encode("ascii").ljust(8, b"\x00")[:8]
    dev_ser_bytes = device_serial.encode("ascii").ljust(8, b"\x00")[:8]

    packet = bytearray()
    packet.extend(POSTGW_HEADER)                       # 6
    packet.extend(struct.pack(">I", 281))              # 4  length = 281
    packet.extend(struct.pack(">H", POSTGW_PACKET_TYPE))  # 2
    packet.extend(b"\x00\x00")                        # 2  padding
    packet.extend(dev_id_bytes)                        # 8
    packet.extend(dev_ser_bytes)                       # 8
    packet.extend(iv)                                  # 16
    packet.extend(ts_bytes)                            # 6  envelope ts
    packet.extend(ciphertext)                          # 240
    # CRC over bytes [0:292] — the entire packet so far
    packet.extend(struct.pack(">H", _crc16_modbus(bytes(packet))))  # 2

    assert len(packet) == 294, f"Packet length {len(packet)} != 294"
    return bytes(packet)


class GoodweLocalSemsRelay:
    """Connects directly to the inverter, reads raw modbus data, syncs to SEMS."""

    def __init__(
        self,
        hass: HomeAssistant,
        inverter_host: str,
        inverter_port: int,
        model_family: str,
        device_header_hex: str,
        device_id: str,
        device_serial: str,
    ) -> None:
        self.hass = hass
        self._inverter_host = inverter_host
        self._inverter_port = inverter_port
        self._model_family = model_family
        self._device_header = bytes.fromhex(device_header_hex)
        self._device_id = device_id
        self._device_serial = device_serial

        self._inverter: Inverter | None = None
        self._consecutive_read_failures: int = 0
        self._last_sems_sync: datetime | None = None
        self._sems_sync_failed: bool = False
        self._last_error: str | None = None
        self._sync_count: int = 0
        self._sync_count_date: str = ""  # date string for daily reset

        # Persistent TCP connection: SEMS only updates the live display while the
        # session remains open. A new connection per packet silently drops live updates.
        self._sems_reader: asyncio.StreamReader | None = None
        self._sems_writer: asyncio.StreamWriter | None = None

        # Latest decoded sensor values (for HA sensor entities)
        self.last_runtime_data: dict[str, Any] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def async_connect(self) -> bool:
        """Establish connection to the inverter. Returns True on success."""
        try:
            self._inverter = await goodwe_connect(
                self._inverter_host,
                self._inverter_port,
                family=self._model_family if self._model_family != "None" else None,
            )
            _LOGGER.info(
                "Connected to %s inverter at %s (model=%s sn=%s)",
                type(self._inverter).__name__,
                self._inverter_host,
                self._inverter.model_name,
                self._inverter.serial_number,
            )
            return True
        except (InverterError, Exception) as ex:  # pylint: disable=broad-except
            _LOGGER.error("Failed to connect to inverter at %s: %s", self._inverter_host, ex)
            self._inverter = None
            return False

    async def async_sync(self) -> bool:
        """Read inverter data and send one POSTGW packet to SEMS. Returns True on success."""
        if self._inverter is None:
            if not await self.async_connect():
                self._sems_sync_failed = True
                self._last_error = "Inverter not connected"
                return False

        try:
            # ── Step 1: Read decoded register values from inverter ────────────
            try:
                self.last_runtime_data = await self._inverter.read_runtime_data()
                self._consecutive_read_failures = 0
            except Exception as ex:  # pylint: disable=broad-except
                self._consecutive_read_failures += 1
                _LOGGER.warning(
                    "Failed to read inverter runtime data (%d/%d): %s",
                    self._consecutive_read_failures,
                    _MAX_CONSECUTIVE_READ_FAILURES,
                    ex,
                )
                if self._consecutive_read_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                    _LOGGER.error(
                        "Inverter unreachable after %d consecutive failures — "
                        "forcing full reconnect next cycle",
                        self._consecutive_read_failures,
                    )
                    self._inverter = None
                self._sems_sync_failed = True
                self._last_error = f"Inverter read failed: {ex}"
                return False

            # ── Step 2: Build 240-byte plaintext from decoded values ──────────
            plaintext = self._build_plaintext_from_runtime_data(self.last_runtime_data)
            pac_w = int(self.last_runtime_data.get("ppv", 0))
            _LOGGER.info(
                "Built plaintext: ppv=%dW, vpv1=%.1fV, vgrid1=%.1fV, temp=%.1f°C, e_day=%.1fkWh",
                pac_w,
                self.last_runtime_data.get("vpv1", 0),
                self.last_runtime_data.get("vgrid1", 0),
                self.last_runtime_data.get("temperature", 0),
                self.last_runtime_data.get("e_day", 0),
            )
            # ── Step 3: Build + send POSTGW packet ────────────────────────────
            packet = _build_postgw_packet(plaintext, self._device_id, self._device_serial)

            sent = await self._send_to_sems(packet)
            if sent:
                self._last_sems_sync = datetime.now(timezone.utc)
                self._sems_sync_failed = False
                self._last_error = None
                # Reset count at start of each new day (HA local time)
                today = dt_util.now().strftime("%Y-%m-%d")
                if today != self._sync_count_date:
                    self._sync_count = 0
                    self._sync_count_date = today
                self._sync_count += 1
                _LOGGER.info(
                    "POSTGW packet sent to SEMS (sync #%d today, ppv=%dW)",
                    self._sync_count,
                    pac_w,
                )
                return True
            else:
                self._sems_sync_failed = True
                self._last_error = "SEMS rejected packet (NACK)"
                return False

        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Sync failed: %s", ex)
            self._sems_sync_failed = True
            self._last_error = str(ex)
            return False

    def get_status(self) -> dict[str, Any]:
        """Return current sync status for sensor entities."""
        return {
            "last_sync": self._last_sems_sync,
            "failed": self._sems_sync_failed,
            "last_error": self._last_error,
        }

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_plaintext_from_runtime_data(self, data: dict[str, Any]) -> bytes:
        """Build a 240-byte POSTGW plaintext from decoded goodwe runtime data.

        Offsets are empirically verified against captured SEMS packets
        (see CONSOLIDATED_FIELD_VALIDATION.csv). All values are big-endian.
        Raw integer = decoded_value × inverse_scale (e.g. volts × 10 = decivolts).
        """
        pt = bytearray(POSTGW_PLAINTEXT_SIZE)

        # Device header (bytes 0x00–0x14): firmware-level constant
        pt[0:DEVICE_HEADER_SIZE] = self._device_header

        # Timestamp (0x15–0x1A): current HA local time
        now = dt_util.now()
        pt[TIMESTAMP_OFFSET:TIMESTAMP_OFFSET + TIMESTAMP_SIZE] = bytes([
            now.year - 2000, now.month, now.day,
            now.hour, now.minute, now.second,
        ])

        def _u16(offset: int, value: float) -> None:
            struct.pack_into(">H", pt, offset, max(0, min(0xFFFF, round(value))))

        def _i16(offset: int, value: float) -> None:
            struct.pack_into(">h", pt, offset, max(-32768, min(32767, round(value))))

        def _u32(offset: int, value: float) -> None:
            struct.pack_into(">I", pt, offset, max(0, min(0xFFFFFFFF, round(value))))

        def _i32(offset: int, value: float) -> None:
            struct.pack_into(">i", pt, offset, max(-2147483648, min(2147483647, round(value))))

        # ── Write ALL register fields using formula: offset = 0x15 + (reg-30100)*2
        # PV strings (registers 30103-30108)
        _u16(_reg(30103), data.get("vpv1",  0) * 10)     # vpv1  V×10
        _u16(_reg(30104), data.get("ipv1",  0) * 10)     # ipv1  A×10
        _u16(_reg(30105), data.get("vpv2",  0) * 10)     # vpv2  V×10
        _u16(_reg(30106), data.get("ipv2",  0) * 10)     # ipv2  A×10
        _u16(_reg(30107), data.get("vpv3",  0) * 10)     # vpv3  V×10
        _u16(_reg(30108), data.get("ipv3",  0) * 10)     # ipv3  A×10

        # Line-to-line voltages (registers 30115-30117)
        _u16(_reg(30115), data.get("vline1", 0) * 10)    # vline1 V×10
        _u16(_reg(30116), data.get("vline2", 0) * 10)    # vline2 V×10
        _u16(_reg(30117), data.get("vline3", 0) * 10)    # vline3 V×10

        # Grid phase voltages (registers 30118-30120)
        _u16(_reg(30118), data.get("vgrid1", 0) * 10)    # vgrid1 V×10
        _u16(_reg(30119), data.get("vgrid2", 0) * 10)    # vgrid2 V×10
        _u16(_reg(30120), data.get("vgrid3", 0) * 10)    # vgrid3 V×10

        # Grid phase currents (registers 30121-30123)
        _u16(_reg(30121), data.get("igrid1", 0) * 10)    # igrid1 A×10
        _u16(_reg(30122), data.get("igrid2", 0) * 10)    # igrid2 A×10
        _u16(_reg(30123), data.get("igrid3", 0) * 10)    # igrid3 A×10

        # Grid frequencies (registers 30124-30126)
        _u16(_reg(30124), data.get("fgrid1", 0) * 100)   # fgrid1 Hz×100
        _u16(_reg(30125), data.get("fgrid2", 0) * 100)   # fgrid2 Hz×100
        _u16(_reg(30126), data.get("fgrid3", 0) * 100)   # fgrid3 Hz×100

        # Power (register 30128) — use ppv (PV DC output) not AC grid power
        _i16(_reg(30128), data.get("ppv", 0))

        # Work mode (register 30129)
        _u16(_reg(30129), data.get("work_mode", 0))

        # Error codes (register 30130, 4 bytes / 2 registers)
        _u32(_reg(30130), data.get("error_codes", 0))

        # Warning code (register 30132)
        _u16(_reg(30132), data.get("warning_code", 0))

        # Apparent power (register 30133, 4 bytes)
        _u32(_reg(30133), int(data.get("apparent_power", 0)))

        # Reactive power (register 30135, 4 bytes, signed)
        _i32(_reg(30135), int(data.get("reactive_power", 0)))

        # Power factor (register 30139, library divides by 1000 on read → multiply back)
        _u16(_reg(30139), int(data.get("power_factor", 0) * 1000))

        # Temperature (register 30141, ÷10 → ×10, signed)
        _i16(_reg(30141), data.get("temperature", 0) * 10)

        # Energy today (register 30144, kWh×10)
        _u16(_reg(30144), data.get("e_day",   0) * 10)

        # Total energy (register 30145-30146, 4 bytes, kWh×10)
        _u32(_reg(30145), data.get("e_total", 0) * 10)

        # Total hours (register 30147-30148, 4 bytes)
        _u32(_reg(30147), data.get("h_total", 0))

        # Safety country (register 30149)
        _u16(_reg(30149), data.get("safety_country", 0))

        # Funbit (register 30162)
        _u16(_reg(30162), data.get("funbit", 0))

        # DC bus voltages (registers 30163-30164)
        _u16(_reg(30163), data.get("vbus", 0) * 10)      # vbus  V×10
        _u16(_reg(30164), data.get("vnbus", 0) * 10)     # vnbus V×10

        # Derating mode (register 30165-30166, 4 bytes)
        _u32(_reg(30165), data.get("derating_mode", 0))

        # Firmware sentinel tail (0xCD–0xEF, beyond register range)
        pt[_CONSTANT_TAIL_OFFSET:_CONSTANT_TAIL_OFFSET + len(_CONSTANT_TAIL)] = _CONSTANT_TAIL

        _LOGGER.info(
            "Built plaintext (register dump): ppv=%sW, vpv1=%sV, "
            "vgrid1=%sV, temp=%s°C, e_day=%skWh, work_mode=%s",
            data.get("ppv", 0), data.get("vpv1", 0),
            data.get("vgrid1", 0), data.get("temperature", 0),
            data.get("e_day", 0), data.get("work_mode", 0),
        )

        return bytes(pt)

    async def _ensure_sems_connection(self) -> bool:
        """Ensure the persistent TCP connection to SEMS is alive. Returns True if ready."""
        if self._sems_writer is not None and not self._sems_writer.is_closing():
            return True
        try:
            self._sems_reader, self._sems_writer = await asyncio.open_connection(
                SEMS_CLOUD_HOST, SEMS_CLOUD_PORT
            )
            _LOGGER.info("Opened persistent SEMS TCP connection")
            return True
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Failed to connect to SEMS: %s", ex)
            self._sems_reader = None
            self._sems_writer = None
            return False

    async def _close_sems_connection(self) -> None:
        """Close the persistent SEMS TCP connection."""
        if self._sems_writer is not None:
            try:
                self._sems_writer.close()
                await self._sems_writer.wait_closed()
            except Exception:  # pylint: disable=broad-except
                pass
            self._sems_reader = None
            self._sems_writer = None

    async def _send_to_sems(self, packet: bytes) -> bool:
        """Send packet on the persistent SEMS TCP connection and verify the ACK.

        SEMS ACK format (58 bytes, header b"GW"):
          [24:40] IV = server timestamp(6) + zeros(10)
          [40:56] AES-128-CBC payload: all-zeros = ACK, 0x02+zeros = NACK
        """
        # Try to use existing connection, reconnect once on failure.
        for attempt in range(2):
            if not await self._ensure_sems_connection():
                return False
            try:
                assert self._sems_writer is not None
                assert self._sems_reader is not None
                self._sems_writer.write(packet)
                await self._sems_writer.drain()
                try:
                    ack = await asyncio.wait_for(self._sems_reader.read(256), timeout=5.0)
                    if not ack:
                        # EOF: SEMS closed the connection from its end
                        _LOGGER.info("SEMS connection closed by server (EOF) — reconnecting")
                        await self._close_sems_connection()
                        if attempt == 0:
                            continue
                        _LOGGER.error("SEMS connection dropped after send")
                        return False
                    if len(ack) >= 58:
                        iv = ack[24:40]
                        try:
                            cipher = Cipher(
                                algorithms.AES(POSTGW_ENCRYPTION_KEY),
                                modes.CBC(iv),
                                backend=default_backend(),
                            )
                            decrypted = cipher.decryptor().update(ack[40:56]) + cipher.decryptor().finalize()
                            if decrypted[0] == 0x02:
                                _LOGGER.warning(
                                    "SEMS returned NACK (packet rejected). Raw ACK: %s", ack.hex()
                                )
                                return False
                            _LOGGER.debug("SEMS ACK accepted (payload[0]=0x%02x)", decrypted[0])
                        except Exception:  # pylint: disable=broad-except
                            _LOGGER.debug("SEMS ACK received but decrypt failed (raw: %s)", ack.hex())
                    else:
                        _LOGGER.debug("SEMS ACK received (%d bytes, raw: %s)", len(ack), ack.hex())
                    return True
                except asyncio.TimeoutError:
                    _LOGGER.debug("No SEMS ACK (5s timeout) — assuming accepted")
                    return True
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.debug("SEMS send failed on attempt %d: %s", attempt + 1, ex)
                await self._close_sems_connection()
                if attempt == 0:
                    continue  # retry with fresh connection
                _LOGGER.error("Failed to send packet to SEMS: %s", ex)
                return False
        return False
