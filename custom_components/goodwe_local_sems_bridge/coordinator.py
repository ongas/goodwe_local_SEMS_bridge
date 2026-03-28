"""SEMS sync relay for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# POSTGW Protocol Constants
POSTGW_HEADER = b"POSTGW"
POSTGW_PACKET_LENGTH = 281  # Type(2) + Envelope(40) + Ciphertext(240) + CRC(2) - 3 byte offset
POSTGW_PACKET_TYPE = 0x0104  # Data packet type
POSTGW_ENVELOPE_SIZE = 40
POSTGW_CIPHERTEXT_SIZE = 240
POSTGW_TOTAL_SIZE = 6 + 4 + POSTGW_PACKET_LENGTH + 3  # 294 bytes

# Encryption
POSTGW_ENCRYPTION_KEY = bytes([0xFF] * 16)  # AES-128 key (all 255s)

# SEMS Cloud endpoint
SEMS_CLOUD_HOST = "tcp.goodwe-power.com"
SEMS_CLOUD_PORT = 20001


class CRC16Modbus:
    """CRC-16 Modbus calculator for POSTGW packets."""

    @staticmethod
    def calculate(data: bytes) -> int:
        """Calculate CRC-16 Modbus over the supplied bytes.

        Returns the raw 16-bit integer value.  Callers must pack it with
        struct.pack(">H", value) to produce the big-endian wire bytes.
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc  # raw value — do NOT byte-swap here


class POSTGWPacketBuilder:
    """Builds valid POSTGW protocol packets.

    Envelope layout (40 bytes, plaintext — NOT encrypted):
      Bytes  0- 1: 0x0000 (null padding)
      Bytes  2- 9: Device ID (8 bytes, ASCII, null-padded)
      Bytes 10-17: Device Serial (8 bytes, ASCII, null-padded)
      Bytes 18-33: IV (16 bytes: 6-byte LOCAL timestamp + 10 zero bytes)
      Bytes 34-39: Timestamp (same 6 bytes as IV prefix)

    IV format: [year-2000, month, day, hour, minute, second, 0×10]
    Timezone:  LOCAL (system timezone — matches what the inverter uses)
    """

    def __init__(
        self,
        device_id: str,
        device_serial: str,
    ) -> None:
        self.device_id = device_id
        self.device_serial = device_serial

    def _make_iv(self, ts: datetime) -> bytes:
        """Build 16-byte IV from LOCAL timestamp: 6 ts bytes + 10 zeros."""
        return bytes([
            ts.year - 2000,
            ts.month,
            ts.day,
            ts.hour,
            ts.minute,
            ts.second,
        ]) + bytes(10)

    def build_packet(self, plaintext_payload: bytes) -> bytes:
        """Build a complete 294-byte POSTGW packet.

        Args:
            plaintext_payload: Exactly 240 bytes of plaintext modbus data.

        Returns:
            Complete 294-byte POSTGW packet with AES encryption and CRC.
        """
        if len(plaintext_payload) != POSTGW_CIPHERTEXT_SIZE:
            raise ValueError(
                f"Payload must be {POSTGW_CIPHERTEXT_SIZE} bytes, got {len(plaintext_payload)}"
            )

        now = datetime.now()  # LOCAL time
        iv = self._make_iv(now)
        ts_bytes = iv[:6]  # first 6 bytes are the timestamp

        # Encrypt with LOCAL-time IV
        ciphertext = self._encrypt_payload(plaintext_payload, iv)

        # Build envelope (40 bytes, plaintext):
        # [0x0000](2) + device_id(8) + serial(8) + IV(16) + timestamp(6)
        dev_id_bytes = self.device_id.encode("ascii").ljust(8, b"\x00")[:8]
        dev_serial_bytes = self.device_serial.encode("ascii").ljust(8, b"\x00")[:8]
        envelope = b"\x00\x00" + dev_id_bytes + dev_serial_bytes + iv + ts_bytes

        # Assemble packet (without CRC)
        packet = bytearray(
            POSTGW_HEADER
            + struct.pack(">I", POSTGW_PACKET_LENGTH)
            + struct.pack(">H", POSTGW_PACKET_TYPE)
            + envelope
            + ciphertext
        )

        # CRC-16 Modbus over the ENTIRE packet (bytes 0 to 291 inclusive)
        crc_value = CRC16Modbus.calculate(bytes(packet))
        packet.extend(struct.pack(">H", crc_value))

        return bytes(packet)

    def _encrypt_payload(self, plaintext: bytes, iv: bytes) -> bytes:
        """Encrypt plaintext with AES-128-CBC using the supplied IV."""
        cipher = Cipher(
            algorithms.AES(POSTGW_ENCRYPTION_KEY),
            modes.CBC(iv),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        return encryptor.update(plaintext) + encryptor.finalize()


class POSTGWClient:
    """TCP client for sending POSTGW packets to SEMS cloud."""

    async def send_packet_async(self, packet: bytes) -> bool:
        """Send packet to SEMS cloud asynchronously.
        
        Args:
            packet: Complete POSTGW packet (294 bytes)
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            reader, writer = await asyncio.open_connection(SEMS_CLOUD_HOST, SEMS_CLOUD_PORT)
            writer.write(packet)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Failed to send packet to SEMS: %s", ex)
            return False


class GoodweLocalSemsRelay:
    """Relay that syncs Goodwe data to SEMS by sending POSTGW packets.
    
    Reads data from the official Goodwe integration via modbus and relays
    to SEMS cloud using the POSTGW protocol (AA55 protocol over TCP).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        goodwe_entry_id: str,
        sems_username: str,
        sems_password: str,
        sems_station_id: str,
        device_id: str,
        device_serial: str,
    ) -> None:
        """Initialize the relay."""
        self.hass = hass
        self.goodwe_entry_id = goodwe_entry_id
        self.sems_username = sems_username
        self.sems_password = sems_password
        self.sems_station_id = sems_station_id
        self.device_id = device_id
        self.device_serial = device_serial
        
        self._postgw_client = POSTGWClient()
        self._last_sems_sync: datetime | None = None
        self._sems_sync_failed = False
        self._sync_count: int = 0
        self._last_error: str | None = None

    async def async_sync(self) -> bool:
        """Sync latest Goodwe data to SEMS via POSTGW protocol.
        
        Returns True if sync succeeded, False otherwise.
        """
        try:
            # Get data from Goodwe integration
            goodwe_data = await self._get_goodwe_data()
            if goodwe_data is None:
                return False
            
            # Log key values being sent
            _LOGGER.info(
                "Syncing inverter data to SEMS: vpv1=%.1fV ipv1=%.1fA vgrid=%.1fV igrid=%.1fA "
                "pgrid=%dW e_day=%.1fkWh e_total=%.1fkWh temp=%.1f°C",
                goodwe_data.get("vpv1", 0),
                goodwe_data.get("ipv1", 0),
                goodwe_data.get("vgrid", 0),
                goodwe_data.get("igrid", 0),
                goodwe_data.get("pgrid", 0),
                goodwe_data.get("e_day", 0),
                goodwe_data.get("e_total", 0),
                goodwe_data.get("temperature", 0),
            )
            
            # Build POSTGW payload from inverter data
            payload = self._build_postgw_payload(goodwe_data)
            
            # Build and send POSTGW packet
            packet_builder = POSTGWPacketBuilder(
                device_id=self.device_id,
                device_serial=self.device_serial,
            )
            packet = packet_builder.build_packet(payload)
            
            # Send to SEMS
            if await self._postgw_client.send_packet_async(packet):
                self._last_sems_sync = datetime.now(timezone.utc)
                self._sems_sync_failed = False
                self._sync_count += 1
                self._last_error = None
                _LOGGER.info("POSTGW packet sent to SEMS successfully")
                return True
            else:
                self._sems_sync_failed = True
                self._last_error = "Failed to send POSTGW packet"
                _LOGGER.error("Failed to send POSTGW packet to SEMS")
                return False
            
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("SEMS sync failed: %s", ex)
            self._sems_sync_failed = True
            self._last_error = str(ex)
            return False

    async def _get_goodwe_data(self) -> dict[str, Any] | None:
        """Get current data from the official Goodwe integration coordinator.

        The coordinator data dict contains sensor values already decoded to
        physical units by the goodwe library:
          voltage   → float, V   (e.g. vpv1, vgrid)
          current   → float, A   (e.g. ipv1, igrid)
          frequency → float, Hz  (e.g. fgrid)
          power     → int,   W   (e.g. ppv1, pgrid, total_inverter_power)
          temp      → float, °C  (e.g. temperature)
          energy    → float, kWh (e.g. e_day, e_total)

        Returns:
            Dict with inverter sensor data, or None if unavailable.
        """
        try:
            goodwe_entry = self.hass.config_entries.async_get_entry(self.goodwe_entry_id)

            if goodwe_entry is None:
                _LOGGER.warning(
                    "Goodwe config entry '%s' not found - was it removed?",
                    self.goodwe_entry_id,
                )
                return None

            try:
                goodwe_coordinator = goodwe_entry.runtime_data.coordinator
            except (AttributeError, RuntimeError):
                _LOGGER.warning(
                    "Goodwe entry '%s' (%s) has no runtime data yet - "
                    "it may still be loading or failed to connect to the inverter (state: %s)",
                    self.goodwe_entry_id,
                    goodwe_entry.title,
                    goodwe_entry.state,
                )
                return None

            if goodwe_coordinator.data is None:
                _LOGGER.debug("No data from Goodwe coordinator yet")
                return None

            return goodwe_coordinator.data

        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Failed to get Goodwe data: %s", ex)
            return None

    def _build_postgw_payload(self, goodwe_data: dict[str, Any]) -> bytes:
        """Build 240-byte POSTGW plaintext payload from HA Goodwe coordinator data.

        Field names match the goodwe library sensor IDs (as returned by
        inverter.read_runtime_data() and stored in coordinator.data).
        Values are already in physical units: V, A, Hz, W, °C, kWh.

        Wire encoding (big-endian throughout, empirically verified offsets):

          0x00–0x05  Timestamp           6 bytes  [y-2000,M,D,H,m,s] LOCAL
          0x06–0x07  vpv1                uint16   decivolts  (value × 10)
          0x08–0x09  ipv1                uint16   deciamps   (value × 10)
          0x0C–0x0D  vpv2                uint16   decivolts
          0x0E–0x0F  ipv2                uint16   deciamps
          0x2A–0x2B  vgrid  (L1)         uint16   decivolts
          0x2C–0x2D  vgrid2 (L2)         uint16   decivolts
          0x2E–0x2F  vgrid3 (L3)         uint16   decivolts
          0x30–0x31  igrid  (L1 signed)  int16    deciamps
          0x32–0x33  igrid2 (L2 signed)  int16    deciamps
          0x34–0x35  igrid3 (L3 signed)  int16    deciamps
          0x36–0x37  fgrid  (L1)         uint16   centihertz (value × 100)
          0x38–0x39  fgrid2 (L2)         uint16   centihertz
          0x3A–0x3B  fgrid3 (L3)         uint16   centihertz
          0x4D–0x4E  total_inverter_power int16   watts      (verified offset!)
          0x5A–0x5B  temperature         int16    decidegrees (value × 10)
          0x5E–0x5F  e_day               uint16   hectowatt-hours (value × 100)
          0x60–0x63  e_total             uint32   hectowatt-hours (value × 100)
        """
        now = datetime.now()
        payload = bytearray(POSTGW_CIPHERTEXT_SIZE)

        try:
            # ── Timestamp (6 bytes at 0x00) ────────────────────────────────
            payload[0x00:0x06] = bytes([
                now.year - 2000,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
            ])

            # ── PV String 1 (offsets 0x06, 0x08) ──────────────────────────
            self._write_register(
                payload, 0x06, goodwe_data.get("vpv1", 0), DataFormat.VOLTAGE)
            self._write_register(
                payload, 0x08, goodwe_data.get("ipv1", 0), DataFormat.CURRENT)

            # ── PV String 2 (offsets 0x0C, 0x0E) ──────────────────────────
            self._write_register(
                payload, 0x0C, goodwe_data.get("vpv2", 0), DataFormat.VOLTAGE)
            self._write_register(
                payload, 0x0E, goodwe_data.get("ipv2", 0), DataFormat.CURRENT)

            # ── Grid Voltages L1/L2/L3 (0x2A–0x2F) ───────────────────────
            self._write_register(
                payload, 0x2A, goodwe_data.get("vgrid", 0), DataFormat.VOLTAGE)
            self._write_register(
                payload, 0x2C, goodwe_data.get("vgrid2", 0), DataFormat.VOLTAGE)
            self._write_register(
                payload, 0x2E, goodwe_data.get("vgrid3", 0), DataFormat.VOLTAGE)

            # ── Grid Currents L1/L2/L3 signed (0x30–0x35) ─────────────────
            self._write_register(
                payload, 0x30, goodwe_data.get("igrid", 0), DataFormat.CURRENT_SIGNED)
            self._write_register(
                payload, 0x32, goodwe_data.get("igrid2", 0), DataFormat.CURRENT_SIGNED)
            self._write_register(
                payload, 0x34, goodwe_data.get("igrid3", 0), DataFormat.CURRENT_SIGNED)

            # ── Grid Frequencies L1/L2/L3 (0x36–0x3B) ────────────────────
            self._write_register(
                payload, 0x36, goodwe_data.get("fgrid", 0), DataFormat.FREQUENCY)
            self._write_register(
                payload, 0x38, goodwe_data.get("fgrid2", 0), DataFormat.FREQUENCY)
            self._write_register(
                payload, 0x3A, goodwe_data.get("fgrid3", 0), DataFormat.FREQUENCY)

            # ── Total Inverter Power (0x4D–0x4E, verified offset) ─────────
            self._write_register(
                payload, 0x4D, goodwe_data.get("total_inverter_power", 0),
                DataFormat.POWER_SIGNED)

            # ── Temperature (0x5A–0x5B) ───────────────────────────────────
            self._write_register(
                payload, 0x5A, goodwe_data.get("temperature", 0), DataFormat.TEMPERATURE)

            # ── Energy today (0x5E–0x5F, hectowatt-hours) ─────────────────
            self._write_register(
                payload, 0x5E, goodwe_data.get("e_day", 0), DataFormat.ENERGY_DAY)

            # ── Energy total (0x60–0x63, uint32 hectowatt-hours) ──────────
            self._write_register(
                payload, 0x60, goodwe_data.get("e_total", 0), DataFormat.ENERGY_TOTAL)

            _LOGGER.debug(
                "Built POSTGW payload: power=%sW temp=%s°C e_day=%skWh",
                goodwe_data.get("total_inverter_power"),
                goodwe_data.get("temperature"),
                goodwe_data.get("e_day"),
            )
            return bytes(payload)

        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Failed to build POSTGW payload: %s", ex)
            return bytes(payload)

    def _write_register(
        self, payload: bytearray, offset: int, value: float, data_format: "DataFormat"
    ) -> None:
        """Write a value to the payload at the specified offset.
        
        Args:
            payload: Payload bytearray to write to
            offset: Byte offset in payload
            value: Value to write (will be formatted according to data_format)
            data_format: Format specification for encoding
        """
        try:
            encoded = data_format.encode(value)
            if offset + len(encoded) <= len(payload):
                payload[offset : offset + len(encoded)] = encoded
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("Failed to write register at offset %d: %s", offset, ex)

    def get_status(self) -> dict[str, Any]:
        """Get the current sync status."""
        return {
            "last_sync": self._last_sems_sync,
            "failed": self._sems_sync_failed,
        }


class DataFormat:
    """Wire-encoding helpers for POSTGW plaintext fields.

    Input values come from the HA goodwe coordinator, already in physical
    units (V, A, Hz, W, °C, kWh).  Each encoder converts to the on-wire
    big-endian integer format expected by the SEMS server.

    Scaling verified against goodwe library sensor.py and empirically
    confirmed via MITM packet captures:
      Voltage   : wire = int(V  × 10)   → uint16 BE  (decivolts)
      Current   : wire = int(A  × 10)   → uint16/int16 BE  (deciamps)
      Frequency : wire = int(Hz × 100)  → uint16 BE  (centihertz)
      Power     : wire = int(W)         → int16 BE   (watts, signed)
      Temperature: wire = int(°C × 10)  → int16 BE   (decidegrees)
      Energy day: wire = int(kWh × 100) → uint16 BE  (hectowatt-hours)
      Energy tot: wire = int(kWh × 100) → uint32 BE  (hectowatt-hours)
    """

    @staticmethod
    def encode_voltage(value: float) -> bytes:
        """V → decivolts, uint16 BE."""
        int_val = int(float(value) * 10)
        return struct.pack(">H", max(0, min(int_val, 0xFFFF)))

    @staticmethod
    def encode_current(value: float) -> bytes:
        """A → deciamps, uint16 BE (unsigned, for PV strings)."""
        int_val = int(float(value) * 10)
        return struct.pack(">H", max(0, min(int_val, 0xFFFF)))

    @staticmethod
    def encode_current_signed(value: float) -> bytes:
        """A → deciamps, int16 BE (signed, for grid current — can be negative on import)."""
        int_val = int(float(value) * 10)
        return struct.pack(">h", max(-32768, min(int_val, 32767)))

    @staticmethod
    def encode_frequency(value: float) -> bytes:
        """Hz → centihertz, uint16 BE."""
        int_val = int(float(value) * 100)
        return struct.pack(">H", max(0, min(int_val, 0xFFFF)))

    @staticmethod
    def encode_power_signed(value: float) -> bytes:
        """W → watts, int16 BE (signed — negative when consuming from grid)."""
        int_val = int(float(value))
        return struct.pack(">h", max(-32768, min(int_val, 32767)))

    @staticmethod
    def encode_temperature(value: float) -> bytes:
        """°C → decidegrees, int16 BE."""
        int_val = int(float(value) * 10)
        return struct.pack(">h", max(-32768, min(int_val, 32767)))

    @staticmethod
    def encode_energy_day(value: float) -> bytes:
        """kWh → hectowatt-hours (kWh × 100), uint16 BE."""
        int_val = int(float(value) * 100)
        return struct.pack(">H", max(0, min(int_val, 0xFFFF)))

    @staticmethod
    def encode_energy_total(value: float) -> bytes:
        """kWh → hectowatt-hours (kWh × 100), uint32 BE."""
        int_val = int(float(value) * 100)
        return struct.pack(">I", max(0, min(int_val, 0xFFFFFFFF)))

    VOLTAGE = encode_voltage.__func__
    CURRENT = encode_current.__func__
    CURRENT_SIGNED = encode_current_signed.__func__
    FREQUENCY = encode_frequency.__func__
    POWER_SIGNED = encode_power_signed.__func__
    TEMPERATURE = encode_temperature.__func__
    ENERGY_DAY = encode_energy_day.__func__
    ENERGY_TOTAL = encode_energy_total.__func__
