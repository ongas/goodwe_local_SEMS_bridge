"""AA55 Protocol MITM Proxy - Intercepts inverter data and relays to SEMS."""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from homeassistant.core import HomeAssistant

from .modbus_unpacker import format_aa55_data

_LOGGER = logging.getLogger(__name__)

# AA55 Protocol Constants
AA55_HEADER = bytes([0xAA, 0x55])
AA55_TARGET_ADDR = 0x7F  # Target address for data upload
AA55_SOURCE_ADDR = 0xAB  # Wi-Fi module
AA55_CONTROL_CODE = 0x02  # Write/upload
AA55_FUNCTION_CODE = 0x03  # Modbus function 3 (read holding registers) + 128 = 0x83

# SEMS Cloud endpoint
SEMS_CLOUD_HOST = "tcp.goodwe-power.com"
SEMS_CLOUD_PORT = 20001

# Modbus register base address for MT series (common for most inverters)
MODBUS_BASE_ADDR = 0x0200


class CRC16:
    """CRC-16 calculator for AA55 packets."""

    @staticmethod
    def calculate(data: bytes) -> int:
        """Calculate CRC-16-CCITT for AA55 packets.
        
        Args:
            data: Bytes to calculate CRC for (excludes the 2-byte CRC itself)
            
        Returns:
            16-bit CRC value
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc


class AA55Packet:
    """Represents an AA55 protocol packet."""

    def __init__(self, raw_bytes: bytes | None = None) -> None:
        """Initialize packet from raw bytes or create new."""
        self.header = AA55_HEADER
        self.target_addr = AA55_TARGET_ADDR
        self.source_addr = AA55_SOURCE_ADDR
        self.control_code = AA55_CONTROL_CODE
        self.function_code = AA55_FUNCTION_CODE
        self.payload = bytearray()
        self.crc = 0

        if raw_bytes:
            self._parse(raw_bytes)

    def _parse(self, raw_bytes: bytes) -> None:
        """Parse raw bytes into packet structure."""
        if len(raw_bytes) < 10:
            raise ValueError(f"Packet too short: {len(raw_bytes)} bytes")

        if raw_bytes[0:2] != AA55_HEADER:
            raise ValueError(f"Invalid header: {raw_bytes[0:2].hex()}")

        self.target_addr = raw_bytes[2]
        self.source_addr = raw_bytes[3]
        self.control_code = raw_bytes[4]
        self.function_code = raw_bytes[5]

        # Payload is everything between function code and CRC
        payload_end = len(raw_bytes) - 2
        self.payload = bytearray(raw_bytes[6:payload_end])

        # CRC is last 2 bytes (big-endian)
        self.crc = (raw_bytes[-2] << 8) | raw_bytes[-1]

    def to_bytes(self) -> bytes:
        """Convert packet to bytes with calculated CRC."""
        # Build packet without CRC
        packet_data = (
            self.header
            + bytes([self.target_addr])
            + bytes([self.source_addr])
            + bytes([self.control_code])
            + bytes([self.function_code])
            + self.payload
        )

        # Calculate CRC on packet data (without CRC bytes itself)
        crc = CRC16.calculate(packet_data)

        # Append CRC in big-endian format
        packet_data += bytes([(crc >> 8) & 0xFF, crc & 0xFF])

        return packet_data

    def verify_crc(self) -> bool:
        """Verify packet CRC."""
        packet_data = (
            self.header
            + bytes([self.target_addr])
            + bytes([self.source_addr])
            + bytes([self.control_code])
            + bytes([self.function_code])
            + self.payload
        )
        calculated_crc = CRC16.calculate(packet_data)
        return calculated_crc == self.crc


class AA55Proxy:
    """MITM Proxy for AA55 protocol - intercepts and relays inverter data to SEMS."""

    def __init__(
        self,
        hass: HomeAssistant,
        goodwe_entry_id: str,
        local_port: int = 20001,
        relay_to_sems: bool = False,
    ) -> None:
        """Initialize the proxy.
        
        Args:
            hass: Home Assistant instance
            goodwe_entry_id: Reference to GoodWe integration entry
            local_port: Local port to listen on (default 20001)
            relay_to_sems: Whether to relay packets to SEMS cloud (default False - disabled for testing)
        """
        self.hass = hass
        self.goodwe_entry_id = goodwe_entry_id
        self.local_port = local_port
        self.relay_to_sems = relay_to_sems
        self._server: asyncio.Server | None = None
        self._running = False
        self._last_packet_time: float | None = None

    async def start(self) -> bool:
        """Start the MITM proxy server.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            self._server = await asyncio.start_server(
                self._handle_inverter_connection,
                "0.0.0.0",
                self.local_port,
            )
            self._running = True
            relay_status = "enabled" if self.relay_to_sems else "disabled"
            _LOGGER.info(
                "AA55 MITM Proxy started on port %d (SEMS relay: %s)",
                self.local_port,
                relay_status,
            )
            return True
        except Exception as ex:
            _LOGGER.error("Failed to start AA55 proxy: %s", ex)
            return False

    async def stop(self) -> None:
        """Stop the proxy server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            _LOGGER.info("AA55 MITM Proxy stopped")

    async def _handle_inverter_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle connection from inverter WiFi module.
        
        This accepts the incoming AA55 packet, replaces the payload with
        latest data from the GoodWe coordinator, and relays to SEMS cloud.
        """
        addr = writer.get_extra_info("peername")
        _LOGGER.debug("Inverter connection from %s", addr)

        try:
            # Receive the AA55 packet from inverter
            packet_data = await asyncio.wait_for(
                reader.read(1024), timeout=10.0
            )

            if not packet_data:
                _LOGGER.debug("No data received from inverter")
                return

            _LOGGER.debug("Received packet from inverter: %d bytes", len(packet_data))

            # Parse incoming packet
            try:
                incoming_packet = AA55Packet(packet_data)
            except ValueError as ex:
                _LOGGER.warning("Failed to parse AA55 packet: %s", ex)
                return

            # Verify CRC
            if not incoming_packet.verify_crc():
                _LOGGER.warning("CRC verification failed on incoming packet")
                # Still process it, but log warning

            # Decrypt and log payload data for inspection (non-invasive)
            try:
                plaintext = self._decrypt_aa55_payload(incoming_packet.payload)
                if plaintext and len(plaintext) >= 112:
                    _LOGGER.info("Intercepted inverter data:\n%s", format_aa55_data(plaintext[:112]))
                else:
                    _LOGGER.debug("Could not decrypt payload or payload too short")
            except Exception as ex:
                _LOGGER.debug("Error decrypting payload for inspection: %s", ex)

            # Get latest data from GoodWe coordinator
            outgoing_packet = await self._create_updated_packet(incoming_packet)

            if not outgoing_packet:
                _LOGGER.warning("Failed to create updated packet")
                return

            # Send to SEMS cloud (if enabled)
            if self.relay_to_sems:
                success = await self._forward_to_sems(outgoing_packet.to_bytes())
                if success:
                    _LOGGER.debug("Successfully forwarded packet to SEMS")
                else:
                    _LOGGER.warning("Failed to forward packet to SEMS")
            else:
                _LOGGER.debug("SEMS relay disabled - packet not forwarded (received %d bytes)", len(packet_data))

        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for inverter data")
        except Exception as ex:
            _LOGGER.error("Error handling inverter connection: %s", ex)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _create_updated_packet(
        self, incoming_packet: AA55Packet
    ) -> AA55Packet | None:
        """Create a new packet with payload replaced by latest HA data.
        
        Args:
            incoming_packet: The original AA55 packet from the inverter
            
        Returns:
            Updated AA55Packet with latest data, or None if failed
        """
        # Create new packet with same structure
        outgoing_packet = AA55Packet()
        outgoing_packet.target_addr = incoming_packet.target_addr
        outgoing_packet.source_addr = incoming_packet.source_addr
        outgoing_packet.control_code = incoming_packet.control_code
        outgoing_packet.function_code = incoming_packet.function_code

        # Get latest inverter data from GoodWe coordinator
        try:
            goodwe_data = self._get_goodwe_data()
            if not goodwe_data:
                _LOGGER.warning("No GoodWe coordinator data available")
                # Fall back to original payload
                outgoing_packet.payload = incoming_packet.payload
                return outgoing_packet

            # TODO: Replace payload with data from goodwe_data
            # This requires mapping inverter sensor values to Modbus register bytes
            outgoing_packet.payload = self._build_payload_from_goodwe_data(goodwe_data)

            return outgoing_packet

        except Exception as ex:
            _LOGGER.error("Failed to create updated packet: %s", ex)
            return None

    def _get_goodwe_data(self) -> dict[str, Any] | None:
        """Get latest inverter data from GoodWe coordinator.
        
        Returns:
            Dictionary with inverter data, or None if not available
        """
        try:
            goodwe_runtime_data = self.hass.data.get("goodwe", {}).get(
                self.goodwe_entry_id
            )

            if not goodwe_runtime_data:
                return None

            coordinator = goodwe_runtime_data.coordinator

            if coordinator.data is None:
                return None

            return coordinator.data

        except Exception as ex:
            _LOGGER.error("Failed to get GoodWe data: %s", ex)
            return None

    def _decrypt_aa55_payload(self, encrypted_payload: bytearray) -> bytes | None:
        """Decrypt AA55 encrypted payload.
        
        AA55 uses AES-128-CBC encryption with:
        - Key: 0xFF repeated 16 times
        - IV: Extracted from packet bytes 30-45
        - Ciphertext: Payload bytes 52-164 (112 bytes encrypted)
        - Plaintext: 112 bytes of Modbus register data
        
        Args:
            encrypted_payload: Full encrypted AA55 payload
            
        Returns:
            Decrypted 112-byte plaintext payload, or None if failed
        """
        try:
            if len(encrypted_payload) < 166:
                _LOGGER.debug("Payload too short for decryption: %d bytes", len(encrypted_payload))
                return None

            # Extract IV from bytes 30-45 (16 bytes)
            iv = bytes(encrypted_payload[30:46])
            
            # Extract ciphertext from bytes 52-164 (112 bytes encrypted)
            ciphertext = bytes(encrypted_payload[52:164])
            
            # Encryption key is 0xFF repeated 16 times
            key = bytes([0xFF] * 16)
            
            # Create AES-128-CBC cipher
            cipher = Cipher(
                algorithms.AES(key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            # Decrypt
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            
            _LOGGER.debug("Successfully decrypted AA55 payload: %d bytes", len(plaintext))
            return plaintext
            
        except Exception as ex:
            _LOGGER.debug("Error decrypting AA55 payload: %s", ex)
            return None

    def _build_payload_from_goodwe_data(
        self, goodwe_data: dict[str, Any]
    ) -> bytearray:
        """Build AA55 payload from GoodWe coordinator data.
        
        This maps the HA sensor values back to Modbus register format
        and constructs the payload bytes.
        
        Args:
            goodwe_data: Inverter data from GoodWe coordinator
            
        Returns:
            Payload bytearray ready for AA55 packet
        """
        # TODO: Implement payload construction from goodwe_data
        # For now, return empty payload as placeholder
        payload = bytearray()

        # TODO: Map sensor values to Modbus registers:
        # - Power values (ppv, pgrid, pbattery)
        # - Energy totals (e_total, e_day)
        # - Voltage/current values
        # - Status fields
        # - Temperatures

        return payload

    async def _forward_to_sems(self, packet_bytes: bytes) -> bool:
        """Forward AA55 packet to SEMS cloud.
        
        Args:
            packet_bytes: The complete AA55 packet to send
            
        Returns:
            True if successful, False otherwise
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(SEMS_CLOUD_HOST, SEMS_CLOUD_PORT),
                timeout=5.0,
            )

            writer.write(packet_bytes)
            await writer.drain()

            _LOGGER.debug(
                "Forwarded %d bytes to SEMS cloud", len(packet_bytes)
            )

            # Wait briefly for response (graceful close)
            await asyncio.wait_for(reader.read(1024), timeout=1.0)

            writer.close()
            await writer.wait_closed()

            return True

        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout connecting to SEMS cloud")
            return False
        except Exception as ex:
            _LOGGER.error("Failed to forward to SEMS: %s", ex)
            return False

    def get_status(self) -> dict[str, Any]:
        """Get current proxy status."""
        return {
            "running": self._running,
            "local_port": self.local_port,
            "relay_to_sems": self.relay_to_sems,
            "sems_host": SEMS_CLOUD_HOST if self.relay_to_sems else "disabled",
            "sems_port": SEMS_CLOUD_PORT if self.relay_to_sems else "disabled",
        }
