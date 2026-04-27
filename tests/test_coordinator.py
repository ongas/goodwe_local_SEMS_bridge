"""Tests for coordinator.py — pure functions and relay state management."""

from __future__ import annotations

import asyncio
import struct
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.goodwe_local_sems_bridge.coordinator import (
    DEVICE_HEADER_SIZE,
    POSTGW_ENCRYPTION_KEY,
    POSTGW_HEADER,
    POSTGW_PACKET_TYPE,
    POSTGW_PLAINTEXT_SIZE,
    TIMESTAMP_OFFSET,
    TIMESTAMP_SIZE,
    GoodweLocalSemsRelay,
    _aes_encrypt,
    _build_postgw_packet,
    _CONSTANT_TAIL,
    _CONSTANT_TAIL_OFFSET,
    _crc16_modbus,
    _reg,
)
from custom_components.goodwe_local_sems_bridge.const import (
    KNOWN_DT_DEVICE_HEADER_HEX,
)

from tests.conftest import _sample_runtime_data


# ── _reg() offset formula ────────────────────────────────────────────────────

class TestRegFormula:
    """Test the plaintext offset formula: 0x15 + (register - 30100) * 2."""

    def test_register_30100(self):
        assert _reg(30100) == 0x15

    def test_register_30103(self):
        # vpv1: (30103 - 30100) * 2 = 6 → 0x15 + 6 = 0x1B
        assert _reg(30103) == 0x1B

    def test_register_30128(self):
        # ppv: (30128 - 30100) * 2 = 56 → 0x15 + 56 = 0x4D
        assert _reg(30128) == 0x4D

    def test_register_30141(self):
        # temperature: (30141 - 30100) * 2 = 82 → 0x15 + 82 = 0x67
        assert _reg(30141) == 0x67

    def test_register_30144(self):
        # e_day: (30144 - 30100) * 2 = 88 → 0x15 + 88 = 0x6D
        assert _reg(30144) == 0x6D

    def test_register_30145(self):
        # e_total: (30145 - 30100) * 2 = 90 → 0x15 + 90 = 0x6F
        assert _reg(30145) == 0x6F


# ── _crc16_modbus ─────────────────────────────────────────────────────────────

class TestCRC16:
    """Tests for CRC-16 Modbus calculation."""

    def test_empty_data(self):
        result = _crc16_modbus(b"")
        assert result == 0xFFFF  # initial value, no processing

    def test_known_value(self):
        # CRC-16/MODBUS of b"\x01\x03" = 0x2140
        result = _crc16_modbus(b"\x01\x03")
        assert result == 0x2140

    def test_single_byte(self):
        result = _crc16_modbus(b"\x00")
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF

    def test_deterministic(self):
        data = b"POSTGW test data for CRC"
        assert _crc16_modbus(data) == _crc16_modbus(data)

    def test_different_data_different_crc(self):
        assert _crc16_modbus(b"\x00") != _crc16_modbus(b"\x01")


# ── _aes_encrypt ──────────────────────────────────────────────────────────────

class TestAESEncrypt:
    """Tests for AES-128-CBC encryption."""

    def test_output_length_matches_input(self):
        plaintext = bytes(POSTGW_PLAINTEXT_SIZE)
        iv = bytes(16)
        result = _aes_encrypt(plaintext, iv)
        assert len(result) == POSTGW_PLAINTEXT_SIZE

    def test_different_iv_different_ciphertext(self):
        plaintext = bytes(POSTGW_PLAINTEXT_SIZE)
        ct1 = _aes_encrypt(plaintext, bytes(16))
        ct2 = _aes_encrypt(plaintext, bytes([1]) + bytes(15))
        assert ct1 != ct2

    def test_deterministic(self):
        plaintext = b"\xAB" * POSTGW_PLAINTEXT_SIZE
        iv = bytes(16)
        assert _aes_encrypt(plaintext, iv) == _aes_encrypt(plaintext, iv)

    def test_encrypts_data(self):
        """Ciphertext should not equal plaintext (except in degenerate cases)."""
        plaintext = b"\x42" * POSTGW_PLAINTEXT_SIZE
        iv = bytes(16)
        result = _aes_encrypt(plaintext, iv)
        assert result != plaintext


# ── _build_postgw_packet ──────────────────────────────────────────────────────

class TestBuildPostgwPacket:
    """Tests for the full 294-byte packet construction."""

    def _make_plaintext(self) -> bytes:
        """Build a minimal valid plaintext with a timestamp."""
        pt = bytearray(POSTGW_PLAINTEXT_SIZE)
        # Set a timestamp at the expected offset
        pt[TIMESTAMP_OFFSET : TIMESTAMP_OFFSET + TIMESTAMP_SIZE] = bytes(
            [24, 6, 15, 14, 30, 45]
        )
        return bytes(pt)

    def test_packet_length(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        assert len(packet) == 294

    def test_header_bytes(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        assert packet[:6] == b"POSTGW"

    def test_length_field(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        length = struct.unpack(">I", packet[6:10])[0]
        assert length == 281

    def test_packet_type_field(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        ptype = struct.unpack(">H", packet[10:12])[0]
        assert ptype == POSTGW_PACKET_TYPE

    def test_device_id_in_packet(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        dev_id = packet[14:22]
        assert dev_id == b"12345678"

    def test_device_serial_in_packet(self):
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        dev_ser = packet[22:30]
        assert dev_ser == b"ABCDEFGH"

    def test_iv_derived_from_timestamp(self):
        pt = self._make_plaintext()
        packet = _build_postgw_packet(pt, "12345678", "ABCDEFGH")
        ts_bytes = pt[TIMESTAMP_OFFSET : TIMESTAMP_OFFSET + TIMESTAMP_SIZE]
        expected_iv = ts_bytes + bytes(10)
        assert packet[30:46] == expected_iv

    def test_envelope_timestamp(self):
        pt = self._make_plaintext()
        packet = _build_postgw_packet(pt, "12345678", "ABCDEFGH")
        ts_bytes = pt[TIMESTAMP_OFFSET : TIMESTAMP_OFFSET + TIMESTAMP_SIZE]
        assert packet[46:52] == ts_bytes

    def test_ciphertext_region(self):
        """Ciphertext region is 240 bytes starting at offset 52."""
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        ciphertext = packet[52:292]
        assert len(ciphertext) == 240

    def test_crc_at_end(self):
        """Last 2 bytes are CRC-16 of bytes [0:292]."""
        packet = _build_postgw_packet(self._make_plaintext(), "12345678", "ABCDEFGH")
        expected_crc = _crc16_modbus(packet[:292])
        actual_crc = struct.unpack(">H", packet[292:294])[0]
        assert actual_crc == expected_crc

    def test_short_device_id_padded(self):
        packet = _build_postgw_packet(self._make_plaintext(), "ABC", "XY")
        dev_id = packet[14:22]
        assert dev_id == b"ABC\x00\x00\x00\x00\x00"

    def test_long_device_id_truncated(self):
        packet = _build_postgw_packet(self._make_plaintext(), "1234567890", "ABCDEFGHIJ")
        dev_id = packet[14:22]
        assert dev_id == b"12345678"


# ── _build_plaintext_from_runtime_data ────────────────────────────────────────

class TestBuildPlaintext:
    """Tests for plaintext construction from runtime data."""

    def _make_relay(self) -> GoodweLocalSemsRelay:
        """Create a relay instance with mocked hass."""
        hass = MagicMock()
        return GoodweLocalSemsRelay(
            hass=hass,
            inverter_host="192.168.1.100",
            inverter_port=8899,
            model_family="ET",
            device_header_hex=KNOWN_DT_DEVICE_HEADER_HEX,
            device_id="12345678",
            device_serial="ABCDEFGH",
        )

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_plaintext_length(self, mock_dt_util, sample_runtime_data):
        mock_dt_util.now.return_value = datetime(2024, 6, 15, 14, 30, 45)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        assert len(pt) == POSTGW_PLAINTEXT_SIZE

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_device_header(self, mock_dt_util, sample_runtime_data):
        mock_dt_util.now.return_value = datetime(2024, 6, 15, 14, 30, 45)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        expected_header = bytes.fromhex(KNOWN_DT_DEVICE_HEADER_HEX)
        assert pt[:DEVICE_HEADER_SIZE] == expected_header

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_timestamp(self, mock_dt_util, sample_runtime_data):
        mock_dt_util.now.return_value = datetime(2024, 6, 15, 14, 30, 45)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        ts = pt[TIMESTAMP_OFFSET : TIMESTAMP_OFFSET + TIMESTAMP_SIZE]
        assert ts == bytes([24, 6, 15, 14, 30, 45])  # 2024-2000=24

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_vpv1_offset_and_scaling(self, mock_dt_util, sample_runtime_data):
        """vpv1=350.0V → 3500 (×10) at register 30103 → offset 0x1B."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        raw = struct.unpack(">H", pt[0x1B : 0x1B + 2])[0]
        assert raw == 3500

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_vgrid1_offset_and_scaling(self, mock_dt_util, sample_runtime_data):
        """vgrid1=237.5V → 2375 at register 30118 → offset 0x15 + 36 = 0x39."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        offset = 0x15 + (30118 - 30100) * 2  # 0x39
        raw = struct.unpack(">H", pt[offset : offset + 2])[0]
        assert raw == 2375

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_ppv_signed(self, mock_dt_util, sample_runtime_data):
        """ppv=3750 at register 30128 → offset 0x4D, signed 16-bit."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        raw = struct.unpack(">h", pt[0x4D : 0x4D + 2])[0]
        assert raw == 3750

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_temperature_scaling(self, mock_dt_util, sample_runtime_data):
        """temperature=42.5°C → 425 (×10) at register 30141 → offset 0x67."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        raw = struct.unpack(">h", pt[0x67 : 0x67 + 2])[0]
        assert raw == 425  # 42.5 * 10

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_e_day_scaling(self, mock_dt_util, sample_runtime_data):
        """e_day=18.5kWh → 185 (×10) at register 30144 → offset 0x6D."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        raw = struct.unpack(">H", pt[0x6D : 0x6D + 2])[0]
        assert raw == 185

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_e_total_4byte(self, mock_dt_util, sample_runtime_data):
        """e_total=12500.0kWh → 125000 (×10) at register 30145 → offset 0x6F."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        raw = struct.unpack(">I", pt[0x6F : 0x6F + 4])[0]
        assert raw == 125000

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_constant_tail(self, mock_dt_util, sample_runtime_data):
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        tail = pt[_CONSTANT_TAIL_OFFSET : _CONSTANT_TAIL_OFFSET + len(_CONSTANT_TAIL)]
        assert tail == _CONSTANT_TAIL

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_empty_runtime_data(self, mock_dt_util):
        """Missing keys default to 0 — should not raise."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data({})
        assert len(pt) == POSTGW_PLAINTEXT_SIZE

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_negative_reactive_power(self, mock_dt_util, sample_runtime_data):
        """reactive_power=-50 at register 30135, signed 32-bit."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        offset = 0x15 + (30135 - 30100) * 2  # 0x61
        raw = struct.unpack(">i", pt[offset : offset + 4])[0]
        assert raw == -50

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_power_factor_scaling(self, mock_dt_util, sample_runtime_data):
        """power_factor=0.987 → 987 (×1000) at register 30139."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        pt = relay._build_plaintext_from_runtime_data(sample_runtime_data)
        offset = 0x15 + (30139 - 30100) * 2  # 0x65
        raw = struct.unpack(">H", pt[offset : offset + 2])[0]
        assert raw == 987

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_u16_clamping_no_overflow(self, mock_dt_util):
        """Values exceeding 0xFFFF should be clamped, not wrap."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        data = {"vpv1": 7000.0}  # 7000 * 10 = 70000 > 65535
        pt = relay._build_plaintext_from_runtime_data(data)
        raw = struct.unpack(">H", pt[0x1B : 0x1B + 2])[0]
        assert raw == 0xFFFF

    @patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util")
    def test_i16_clamping_negative(self, mock_dt_util):
        """Signed 16-bit values should be clamped to -32768."""
        mock_dt_util.now.return_value = datetime(2024, 1, 1, 0, 0, 0)
        relay = self._make_relay()
        data = {"ppv": -40000}  # exceeds -32768
        pt = relay._build_plaintext_from_runtime_data(data)
        raw = struct.unpack(">h", pt[0x4D : 0x4D + 2])[0]
        assert raw == -32768


# ── GoodweLocalSemsRelay state management ─────────────────────────────────────

class TestRelayStateManagement:
    """Tests for async_sync state transitions."""

    def _make_relay(self) -> GoodweLocalSemsRelay:
        hass = MagicMock()
        return GoodweLocalSemsRelay(
            hass=hass,
            inverter_host="192.168.1.100",
            inverter_port=8899,
            model_family="ET",
            device_header_hex=KNOWN_DT_DEVICE_HEADER_HEX,
            device_id="12345678",
            device_serial="ABCDEFGH",
        )

    @pytest.mark.asyncio
    async def test_sync_without_inverter_fails(self):
        """async_sync returns False and sets error when no inverter connected."""
        relay = self._make_relay()
        relay._inverter = None

        with patch.object(relay, "async_connect", return_value=False):
            result = await relay.async_sync()

        assert result is False
        assert relay._sems_sync_failed is True
        assert relay._last_error == "Inverter not connected"

    @pytest.mark.asyncio
    async def test_sync_reconnects_when_inverter_none(self):
        """async_sync attempts reconnection when _inverter is None."""
        relay = self._make_relay()
        relay._inverter = None

        mock_connect = AsyncMock(return_value=True)
        # After connect succeeds, we need an inverter that can read data
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())

        async def fake_connect():
            relay._inverter = mock_inv
            return True

        with (
            patch.object(relay, "async_connect", side_effect=fake_connect),
            patch.object(relay, "_send_to_sems", return_value=True),
            patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 0, 0)
            result = await relay.async_sync()

        assert result is True

    @pytest.mark.asyncio
    async def test_sync_runtime_read_failure_keeps_inverter(self):
        """A single runtime data read failure keeps _inverter (no immediate reconnect)."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(side_effect=Exception("timeout"))
        relay._inverter = mock_inv

        result = await relay.async_sync()

        assert result is False
        assert relay._inverter is mock_inv  # kept alive
        assert relay._consecutive_read_failures == 1

    @pytest.mark.asyncio
    async def test_sync_runtime_read_failure_reconnects_after_threshold(self):
        """After _MAX_CONSECUTIVE_READ_FAILURES, _inverter is reset for reconnect."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(side_effect=Exception("timeout"))
        relay._inverter = mock_inv
        relay._consecutive_read_failures = 2  # one below threshold

        result = await relay.async_sync()

        assert result is False
        assert relay._inverter is None  # force reconnect
        assert relay._consecutive_read_failures == 3

    @pytest.mark.asyncio
    async def test_sync_success_resets_consecutive_failures(self):
        """Successful read resets the consecutive failure counter."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())
        relay._inverter = mock_inv
        relay._consecutive_read_failures = 2  # had previous failures

        with (
            patch.object(relay, "_send_to_sems", return_value=True),
            patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 0, 0)
            result = await relay.async_sync()

        assert result is True
        assert relay._consecutive_read_failures == 0

    @pytest.mark.asyncio
    async def test_sync_sems_send_failure(self):
        """SEMS send returning False marks sync as failed."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())
        relay._inverter = mock_inv

        with (
            patch.object(relay, "_send_to_sems", return_value=False),
            patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 0, 0)
            result = await relay.async_sync()

        assert result is False
        assert relay._sems_sync_failed is True
        assert relay._last_error == "SEMS rejected packet (NACK)"
        assert relay._inverter is mock_inv  # inverter kept alive on SEMS failure

    @pytest.mark.asyncio
    async def test_sync_success_updates_state(self):
        """Successful sync updates timestamps and counters."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())
        relay._inverter = mock_inv

        with (
            patch.object(relay, "_send_to_sems", return_value=True),
            patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 0, 0)
            result = await relay.async_sync()

        assert result is True
        assert relay._sems_sync_failed is False
        assert relay._last_error is None
        assert relay._last_sems_sync is not None
        assert relay._sync_count == 1

    @pytest.mark.asyncio
    async def test_sync_count_resets_daily(self):
        """Sync count resets when the date changes."""
        relay = self._make_relay()
        relay._sync_count = 50
        relay._sync_count_date = "2024-06-14"  # yesterday
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())
        relay._inverter = mock_inv

        with (
            patch.object(relay, "_send_to_sems", return_value=True),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.dt_util"
            ) as mock_dt,
        ):
            mock_now = datetime(2024, 6, 15, 14, 0, 0)
            mock_dt.now.return_value = mock_now
            result = await relay.async_sync()

        assert result is True
        assert relay._sync_count == 1  # reset from 50 to 0, then incremented

    def test_get_status(self):
        relay = self._make_relay()
        relay._last_sems_sync = datetime(2024, 6, 15, tzinfo=timezone.utc)
        relay._sems_sync_failed = True
        relay._last_error = "test error"

        status = relay.get_status()
        assert status["last_sync"] == relay._last_sems_sync
        assert status["failed"] is True
        assert status["last_error"] == "test error"

    @pytest.mark.asyncio
    async def test_sync_outer_exception_preserves_inverter(self):
        """Unexpected exceptions during SEMS send preserve the inverter object."""
        relay = self._make_relay()
        mock_inv = AsyncMock()
        mock_inv.read_runtime_data = AsyncMock(return_value=_sample_runtime_data())
        relay._inverter = mock_inv

        with (
            patch.object(relay, "_send_to_sems", side_effect=RuntimeError("boom")),
            patch("custom_components.goodwe_local_sems_bridge.coordinator.dt_util") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 0, 0)
            result = await relay.async_sync()

        assert result is False
        assert relay._inverter is mock_inv  # preserved


# ── _send_to_sems protocol tests ─────────────────────────────────────────────

class TestSendToSems:
    """Tests for SEMS TCP protocol handling with mocked streams."""

    def _make_relay(self) -> GoodweLocalSemsRelay:
        hass = MagicMock()
        return GoodweLocalSemsRelay(
            hass=hass,
            inverter_host="192.168.1.100",
            inverter_port=8899,
            model_family="ET",
            device_header_hex=KNOWN_DT_DEVICE_HEADER_HEX,
            device_id="12345678",
            device_serial="ABCDEFGH",
        )

    def _make_ack(self, nack: bool = False) -> bytes:
        """Build a minimal 58-byte SEMS ACK response."""
        ack = bytearray(58)
        ack[:2] = b"GW"
        # IV at [24:40]: 6 timestamp bytes + 10 zeros
        iv = bytes([24, 6, 15, 14, 30, 45]) + bytes(10)
        ack[24:40] = iv
        # Encrypted payload at [40:56]
        payload = bytearray(16)
        if nack:
            payload[0] = 0x02
        ct = _aes_encrypt(bytes(payload), iv)
        ack[40:56] = ct[:16]
        return bytes(ack)

    def _mock_writer(self) -> MagicMock:
        """Create a MagicMock writer with sync methods + async drain."""
        writer = MagicMock()
        writer.write = MagicMock()       # sync
        writer.close = MagicMock()       # sync
        writer.is_closing = MagicMock(return_value=False)  # sync
        writer.drain = AsyncMock()       # async
        writer.wait_closed = AsyncMock() # async
        return writer

    @pytest.mark.asyncio
    async def test_ack_accepted(self):
        relay = self._make_relay()
        mock_writer = self._mock_writer()
        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(return_value=self._make_ack(nack=False))
        relay._sems_writer = mock_writer
        relay._sems_reader = mock_reader

        result = await relay._send_to_sems(b"\x00" * 294)
        assert result is True

    @pytest.mark.asyncio
    async def test_nack_rejected(self):
        relay = self._make_relay()
        mock_writer = self._mock_writer()
        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(return_value=self._make_ack(nack=True))
        relay._sems_writer = mock_writer
        relay._sems_reader = mock_reader

        result = await relay._send_to_sems(b"\x00" * 294)
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_treated_as_success(self):
        relay = self._make_relay()
        mock_writer = self._mock_writer()
        mock_reader = AsyncMock()
        mock_reader.read = AsyncMock(side_effect=asyncio.TimeoutError)
        relay._sems_writer = mock_writer
        relay._sems_reader = mock_reader

        result = await relay._send_to_sems(b"\x00" * 294)
        assert result is True

    @pytest.mark.asyncio
    async def test_eof_triggers_reconnect(self):
        """EOF (empty read) on first attempt triggers reconnect."""
        relay = self._make_relay()

        call_count = 0

        async def mock_read(n):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b""  # EOF
            return self._make_ack()  # success on retry

        mock_writer = self._mock_writer()
        mock_reader = AsyncMock()
        mock_reader.read = mock_read
        relay._sems_writer = mock_writer
        relay._sems_reader = mock_reader

        async def fake_ensure():
            # Simulate reconnect: re-establish reader/writer
            relay._sems_writer = self._mock_writer()
            relay._sems_reader = mock_reader
            return True

        with patch.object(relay, "_ensure_sems_connection", side_effect=fake_ensure):
            result = await relay._send_to_sems(b"\x00" * 294)

        assert result is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_connection_failure(self):
        relay = self._make_relay()
        relay._sems_writer = None
        relay._sems_reader = None

        with patch.object(relay, "_ensure_sems_connection", return_value=False):
            result = await relay._send_to_sems(b"\x00" * 294)

        assert result is False
