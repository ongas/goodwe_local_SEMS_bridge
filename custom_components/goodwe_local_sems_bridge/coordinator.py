"""SEMS relay coordinator for the GoodWe Local SEMS Bridge integration.

Architecture
------------
1. Connect to the inverter directly via the ``goodwe`` Python library.
2. Every sync cycle, call ``inverter._read_from_socket(_READ_RUNNING_DATA)``
   to obtain the raw trimmed modbus response.
3. Construct the 240-byte POSTGW plaintext:
       plaintext = device_header(21) + modbus_data(219)
   The 21-byte device header is a constant per inverter firmware version,
   captured once during config-flow setup.
4. Update the 6-byte embedded timestamp at plaintext[0x15:0x1B] to now
   (local time, matching what the inverter firmware embeds).
5. Build the 294-byte POSTGW packet (AES-128-CBC + CRC-16 Modbus) and
   send it to tcp.goodwe-power.com:20001.

DT-family plaintext size (confirmed):
  DT inverters (e.g. GW25K-MT) return 146 bytes (73 registers × 2) from
  _READ_RUNNING_DATA (register 0x7594, count 0x49). The 240-byte POSTGW
  plaintext needs 219 bytes of modbus data; the remaining 73 bytes are
  filled with KNOWN_DT_PLAINTEXT_TAIL_HEX from const.py — a static
  pointer/sentinel table that is identical across all captured packets for
  this inverter model. Zero-padding those bytes causes SEMS to ACK the
  packet but silently refuse to update the live pac / last_refresh_time
  display (verified March 2026). GoodWe inverters of other families (e.g.
  DNS G3 / ET series) send larger plaintexts using the same protocol.
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

from .const import KNOWN_DT_PLAINTEXT_TAIL_HEX

_LOGGER = logging.getLogger(__name__)

# ── POSTGW protocol constants ────────────────────────────────────────────────

SEMS_CLOUD_HOST = "tcp.goodwe-power.com"
SEMS_CLOUD_PORT = 20001

POSTGW_HEADER = b"POSTGW"
POSTGW_PACKET_TYPE = 0x0104
POSTGW_ENCRYPTION_KEY = bytes([0xFF] * 16)

# DT-family POSTGW plaintext layout (240 bytes, confirmed from MITM captures):
#   [0x00:0x15]  21 bytes  device header (constant per firmware, not readable via modbus)
#   [0x15:0x1B]   6 bytes  embedded timestamp (YY MM DD HH mm ss, local time)
#   [0x1B:0xEF] 213 bytes  modbus sensor data (registers 0x7596+)
#   Total plaintext           = 240 bytes = 15 × 16 (AES block size)
#
# DT inverters (_READ_RUNNING_DATA) return 146 bytes (73 registers × 2,
# regs 0x7594–0x75DC).  The remaining 73 bytes (plaintext[167:240],
# regs 0x75DD–0x7601) must be filled with KNOWN_DT_PLAINTEXT_TAIL_HEX —
# a static pointer/sentinel table written by the inverter firmware.
# Zero-padding this section causes SEMS to silently skip updating the live
# pac / last_refresh_time display (even though it still ACKs the packet).
POSTGW_PLAINTEXT_SIZE = 240
DEVICE_HEADER_SIZE = 21       # bytes 0x00–0x14: firmware constant, 21 bytes
MODBUS_DATA_SIZE = 219        # bytes 0x15–0xEF: 146 real (DT) + 73 zero-padded
TIMESTAMP_OFFSET = 0x15       # 6-byte timestamp inside plaintext
TIMESTAMP_SIZE = 6


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
    """Build a complete 294-byte POSTGW packet from a 240-byte plaintext.

    Packet layout (verified against submit_synthetic_loop.py):
      0-5    POSTGW header (6)
      6-9    Length uint32-BE = 281 (6)
      10-11  Type 0x0104 (2)
      12-13  0x0000 padding (2)
      14-21  Device ID, 8 bytes ASCII null-padded (8)
      22-29  Device Serial, 8 bytes ASCII null-padded (8)
      30-45  IV = timestamp(6) + 10 zero bytes (16)
      46-51  Envelope timestamp, same 6 bytes (6)
      52-291 Ciphertext (240)
      292-293 CRC-16 Modbus over bytes [0:292] (2)
    """
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

        # Persistent TCP connection to SEMS cloud
        # SEMS only updates the live pac/last_refresh_time display while the
        # TCP connection stays open (verified March 2026). A new connection
        # per packet causes SEMS to accept energy data but not live status.
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
            # ── Step 1: Read raw modbus response from inverter ────────────────
            raw_bytes = await self._read_raw_running_data()
            if raw_bytes is None:
                self._inverter = None  # Force reconnect next cycle
                return False

            # Also decode for HA sensor entities
            try:
                self.last_runtime_data = await self._inverter.read_runtime_data()
            except Exception:  # pylint: disable=broad-except
                pass  # Sensor data is secondary; don't fail the sync for it

            # ── Step 2: Build 240-byte plaintext ─────────────────────────────
            plaintext = self._build_plaintext(raw_bytes)

            # ── Step 3: Build + send POSTGW packet ────────────────────────────
            packet = _build_postgw_packet(plaintext, self._device_id, self._device_serial)

            # Read PAC directly from the plaintext we're about to send (plaintext[0x4D:0x4F])
            pac_from_plaintext = int.from_bytes(plaintext[0x4D:0x4F], "big", signed=True)

            sent = await self._send_to_sems(packet, plaintext)
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
                    pac_from_plaintext,
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

    async def _read_raw_running_data(self) -> bytes | None:
        """Read raw running-data bytes from inverter. Returns data padded to MODBUS_DATA_SIZE or None.

        DT-family inverters (25KMT etc.) return 146 bytes (73 registers × 2).
        The POSTGW plaintext needs 219 bytes of modbus data. The missing 73 bytes
        are mostly zeros in real captures (static register pointers + 0xFF sentinels)
        and are not validated by SEMS for physical plausibility. Zero-pad them.

        If the inverter rejects the read (busy — likely the official goodwe integration
        just polled it), wait 2 seconds and retry once before giving up.
        """
        for attempt in range(2):
            try:
                response = await self._inverter._read_from_socket(  # pylint: disable=protected-access
                    self._inverter._READ_RUNNING_DATA  # pylint: disable=protected-access
                )
                raw = response.response_data()
                if len(raw) < 10:
                    _LOGGER.warning("Running data response too short: %d bytes", len(raw))
                    return None
                        # DT inverters return 146 bytes (73 registers); zero-pad to 219.
                # The trailing 73 bytes are structurally unused by SEMS for DT hardware —
                # confirmed safe by A/B test (March 2026).
                if len(raw) < MODBUS_DATA_SIZE:
                    # Pad with the known static tail bytes (NOT zeros).
                    # Zero-padding causes SEMS to skip live pac/last_refresh_time
                    # updates even though it still ACKs the packet.
                    tail = bytes.fromhex(KNOWN_DT_PLAINTEXT_TAIL_HEX)
                    padding = tail[: MODBUS_DATA_SIZE - len(raw)]
                    _LOGGER.debug(
                        "Padding modbus response from %d to %d bytes with DT static tail",
                        len(raw), MODBUS_DATA_SIZE,
                    )
                    raw = raw + padding
                return raw
            except Exception as ex:  # pylint: disable=broad-except
                if attempt == 0:
                    _LOGGER.debug("Read attempt 1 failed (%s), retrying in 2s", ex)
                    await asyncio.sleep(2)
                    self._inverter = None  # Force fresh connection for retry
                    if not await self.async_connect():
                        return None
                else:
                    _LOGGER.error("Failed to read inverter running data: %s", ex)
                    return None

    def _build_plaintext(self, raw_response: bytes) -> bytes:
        """Build the 240-byte POSTGW plaintext.

        plaintext = device_header(21) + raw_response[:219]

        The device header (0x00–0x14) is the 21-byte constant learned at setup.
        raw_response[0:6]   = registers 35100–35102 = inverter-embedded timestamp
        raw_response[6:219] = registers 35103+ = all sensor data

        We then refresh the 6-byte timestamp at plaintext[0x15] with the current
        local time to ensure SEMS doesn't reject stale timestamps.
        """
        plaintext = bytearray(self._device_header + raw_response[:MODBUS_DATA_SIZE])
        assert len(plaintext) == POSTGW_PLAINTEXT_SIZE

        # Overwrite timestamp at 0x15 with current local time (HA configured timezone)
        # Must use dt_util.now() not datetime.now() — the Docker container runs UTC but
        # SEMS expects the inverter's local solar time (e.g. AEDT), matching what the
        # inverter firmware itself embeds.
        now = dt_util.now()  # Returns datetime in HA's configured timezone
        plaintext[TIMESTAMP_OFFSET:TIMESTAMP_OFFSET + TIMESTAMP_SIZE] = bytes([
            now.year - 2000,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        ])
        return bytes(plaintext)

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

    async def _send_to_sems(
        self, packet: bytes, plaintext: bytes  # noqa: ARG002
    ) -> bool:
        """Send packet on the persistent SEMS TCP connection and verify the ACK.

        Maintains a long-lived TCP connection across syncs.  SEMS only updates
        the live pac / last_refresh_time display while the connection is open —
        a new connection per packet causes SEMS to silently drop live updates
        (verified March 2026).

        SEMS responds with a 58-byte ACK packet (header "GW", not "POSTGW"):
          [0:2]   = b"GW"
          [2:6]   = length (uint32 BE)
          [6:8]   = packet type (0x0104)
          [8:16]  = device ID (8 bytes ASCII)
          [16:24] = device serial (8 bytes ASCII)
          [24:30] = server timestamp (6 bytes: sec,min,hour,day,month,year-2000)
          [30:40] = 10 zero bytes (completing 16-byte IV)
          [40:56] = AES-128-CBC encrypted 16-byte payload
          [56:58] = CRC-16 LE

        Decrypting ack[40:56] with IV=ack[24:40] and key=0xFF*16:
          all-zeros  → ACK (accepted)
          0x02+zeros → NACK (rejected)
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
