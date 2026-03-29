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

# Empirically verified plaintext field offsets (absolute, from CONSOLIDATED_FIELD_VALIDATION.csv).
# Raw value = decoded_value × scale_factor (inverse of goodwe library's ÷ scale).
# All fields are big-endian.  4-byte fields use struct '>I' or '>i'.
_PT_vpv1        = 0x1B   # uint16  vpv1   (V × 10)
_PT_ipv1        = 0x1D   # uint16  ipv1   (A × 10)
_PT_vpv2        = 0x1F   # uint16  vpv2   (V × 10)
_PT_ipv2        = 0x21   # uint16  ipv2   (A × 10)
_PT_vpv3        = 0x23   # uint16  vpv3   (V × 10)
_PT_ipv3        = 0x25   # uint16  ipv3   (A × 10)
_PT_vgrid1      = 0x39   # uint16  vgrid1 (V × 10)
_PT_iac1        = 0x32   # uint16  igrid1 (A × 10)
_PT_iac2        = 0x43   # uint16  igrid2 (A × 10)
_PT_fac1        = 0x45   # uint16  fgrid1 (Hz × 100)
_PT_fac2        = 0x45   # same offset — fgrid1/fac1 are the same field
_PT_fac3        = 0x47   # uint16  fgrid3 (Hz × 100)
_PT_pac         = 0x4D   # int16   total_inverter_power (W × 1)
_PT_temperature = 0x67   # int16   temperature (°C × 10)
_PT_e_day       = 0x6D   # uint16  e_day  (kWh × 10 → hWh)
_PT_e_total     = 0x6F   # uint32  e_total (kWh × 10 → hWh)
_PT_h_total     = 0x75   # uint16  h_total (hours × 1)
_PT_vbus        = 0x81   # uint16  vbus   (V × 10)


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
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error("Failed to read inverter runtime data: %s", ex)
                self._inverter = None  # Force reconnect next cycle
                return False

            # ── Step 2: Build 240-byte plaintext from decoded values ──────────
            plaintext = self._build_plaintext_from_runtime_data(self.last_runtime_data)
            pac_w = int(self.last_runtime_data.get("total_inverter_power", 0))

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
                    "POSTGW packet sent to SEMS (sync #%d today, pac=%dW)",
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
            self._inverter = None  # Force reconnect next cycle
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

        # PV string voltages and currents (÷10 in goodwe → ×10 to raw)
        _u16(_PT_vpv1,        data.get("vpv1",  0) * 10)
        _u16(_PT_ipv1,        data.get("ipv1",  0) * 10)
        _u16(_PT_vpv2,        data.get("vpv2",  0) * 10)
        _u16(_PT_ipv2,        data.get("ipv2",  0) * 10)
        _u16(_PT_vpv3,        data.get("vpv3",  0) * 10)
        _u16(_PT_ipv3,        data.get("ipv3",  0) * 10)

        # Grid voltages and currents (÷10 in goodwe → ×10 to raw)
        _u16(_PT_vgrid1,      data.get("vgrid1", 0) * 10)
        _u16(_PT_iac1,        data.get("igrid1", 0) * 10)
        _u16(_PT_iac2,        data.get("igrid2", 0) * 10)

        # Grid frequencies (÷100 in goodwe → ×100 to raw)
        _u16(_PT_fac1,        data.get("fgrid1", 0) * 100)
        _u16(_PT_fac3,        data.get("fgrid3", 0) * 100)

        # Total inverter power (W ×1, signed)
        _i16(_PT_pac,         data.get("total_inverter_power", 0))

        # Temperature (÷10 in goodwe → ×10 to raw, signed)
        _i16(_PT_temperature, data.get("temperature", 0) * 10)

        # Energy today (÷10 in goodwe → ×10 to raw hectowatt-hours)
        _u16(_PT_e_day,       data.get("e_day",   0) * 10)

        # Total energy (÷10 in goodwe → ×10 to raw, 4 bytes)
        _u32(_PT_e_total,     data.get("e_total", 0) * 10)

        # Total hours (×1, fits in uint16)
        _u16(_PT_h_total,     data.get("h_total", 0))

        # DC bus voltage (÷10 in goodwe → ×10 to raw)
        _u16(_PT_vbus,        data.get("vbus", 0) * 10)

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
