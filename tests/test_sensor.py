"""Tests for sensor.py — sensor entity values, icons, and attributes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.goodwe_local_sems_bridge.const import DOMAIN
from custom_components.goodwe_local_sems_bridge.sensor import (
    InverterConnectionStatusSensor,
    SemsSyncCountSensor,
    SemsSyncLastTimeSensor,
    SemsSyncStatusSensor,
    _device_info,
)
from custom_components.goodwe_local_sems_bridge.coordinator import GoodweLocalSemsRelay

from tests.conftest import MOCK_CONFIG_DATA


def _make_relay() -> GoodweLocalSemsRelay:
    hass = MagicMock()
    return GoodweLocalSemsRelay(
        hass=hass,
        inverter_host="192.168.1.100",
        inverter_port=8899,
        model_family="ET",
        device_header_hex="067552755704570000000e000001310001759475c5",
        device_id="12345678",
        device_serial="ABCDEFGH",
    )


def _make_entry() -> MagicMock:
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.title = "GoodWe SEMS Bridge (GW10K-ET)"
    entry.data = MOCK_CONFIG_DATA.copy()
    return entry


# ── DeviceInfo ────────────────────────────────────────────────────────────────

class TestDeviceInfo:
    def test_device_info_identifiers(self):
        entry = _make_entry()
        info = _device_info(entry)
        assert (DOMAIN, "test_entry_id") in info["identifiers"]

    def test_device_info_name(self):
        entry = _make_entry()
        info = _device_info(entry)
        assert info["name"] == "GoodWe SEMS Bridge (GW10K-ET)"


# ── InverterConnectionStatusSensor ────────────────────────────────────────────

class TestConnectionStatusSensor:
    def test_connected(self):
        relay = _make_relay()
        relay._inverter = MagicMock()
        relay._inverter.model_name = "GW10K-ET"
        relay._inverter.serial_number = "12345678ABCDEFGH"
        sensor = InverterConnectionStatusSensor(relay, _make_entry())

        assert sensor.native_value == "Connected"
        assert sensor.icon == "mdi:lan-connect"

    def test_disconnected(self):
        relay = _make_relay()
        relay._inverter = None
        sensor = InverterConnectionStatusSensor(relay, _make_entry())

        assert sensor.native_value == "Disconnected"
        assert sensor.icon == "mdi:lan-disconnect"

    def test_extra_attrs_when_connected(self):
        relay = _make_relay()
        relay._inverter = MagicMock()
        relay._inverter.model_name = "GW10K-ET"
        relay._inverter.serial_number = "12345678ABCDEFGH"
        relay._last_error = None
        sensor = InverterConnectionStatusSensor(relay, _make_entry())

        attrs = sensor.extra_state_attributes
        assert attrs["model"] == "GW10K-ET"
        assert attrs["serial"] == "12345678ABCDEFGH"

    def test_extra_attrs_when_disconnected_with_error(self):
        relay = _make_relay()
        relay._inverter = None
        relay._last_error = "Connection refused"
        sensor = InverterConnectionStatusSensor(relay, _make_entry())

        attrs = sensor.extra_state_attributes
        assert "model" not in attrs
        assert attrs["last_error"] == "Connection refused"

    def test_extra_attrs_no_error(self):
        relay = _make_relay()
        relay._inverter = None
        relay._last_error = None
        sensor = InverterConnectionStatusSensor(relay, _make_entry())

        attrs = sensor.extra_state_attributes
        assert "last_error" not in attrs

    def test_unique_id(self):
        sensor = InverterConnectionStatusSensor(_make_relay(), _make_entry())
        assert sensor.unique_id == "test_entry_id_connection_status"


# ── SemsSyncStatusSensor ──────────────────────────────────────────────────────

class TestSyncStatusSensor:
    def test_pending(self):
        relay = _make_relay()
        relay._last_sems_sync = None
        sensor = SemsSyncStatusSensor(relay, _make_entry())
        assert sensor.native_value == "Pending"
        assert sensor.icon == "mdi:cloud-clock"

    def test_ok(self):
        relay = _make_relay()
        relay._last_sems_sync = datetime.now(timezone.utc)
        relay._sems_sync_failed = False
        sensor = SemsSyncStatusSensor(relay, _make_entry())
        assert sensor.native_value == "OK"
        assert sensor.icon == "mdi:cloud-check"

    def test_failed(self):
        relay = _make_relay()
        relay._last_sems_sync = datetime.now(timezone.utc)
        relay._sems_sync_failed = True
        sensor = SemsSyncStatusSensor(relay, _make_entry())
        assert sensor.native_value == "Failed"
        assert sensor.icon == "mdi:cloud-alert"

    def test_extra_attrs_with_error(self):
        relay = _make_relay()
        relay._last_error = "NACK"
        sensor = SemsSyncStatusSensor(relay, _make_entry())
        assert sensor.extra_state_attributes == {"last_error": "NACK"}

    def test_extra_attrs_no_error(self):
        relay = _make_relay()
        relay._last_error = None
        sensor = SemsSyncStatusSensor(relay, _make_entry())
        assert sensor.extra_state_attributes == {}

    def test_unique_id(self):
        sensor = SemsSyncStatusSensor(_make_relay(), _make_entry())
        assert sensor.unique_id == "test_entry_id_sync_status"


# ── SemsSyncLastTimeSensor ────────────────────────────────────────────────────

class TestSyncLastTimeSensor:
    def test_no_sync_yet(self):
        relay = _make_relay()
        relay._last_sems_sync = None
        sensor = SemsSyncLastTimeSensor(relay, _make_entry())
        assert sensor.native_value is None

    def test_with_sync_time(self):
        ts = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        relay = _make_relay()
        relay._last_sems_sync = ts
        sensor = SemsSyncLastTimeSensor(relay, _make_entry())
        assert sensor.native_value == ts

    def test_unique_id(self):
        sensor = SemsSyncLastTimeSensor(_make_relay(), _make_entry())
        assert sensor.unique_id == "test_entry_id_last_sync"


# ── SemsSyncCountSensor ──────────────────────────────────────────────────────

class TestSyncCountSensor:
    def test_initial_value(self):
        relay = _make_relay()
        relay._sync_count = 0
        sensor = SemsSyncCountSensor(relay, _make_entry())
        assert sensor.native_value == 0

    def test_after_syncs(self):
        relay = _make_relay()
        relay._sync_count = 42
        sensor = SemsSyncCountSensor(relay, _make_entry())
        assert sensor.native_value == 42

    def test_extra_attrs_date(self):
        relay = _make_relay()
        relay._sync_count_date = "2024-06-15"
        sensor = SemsSyncCountSensor(relay, _make_entry())
        assert sensor.extra_state_attributes == {"date": "2024-06-15"}

    def test_unique_id(self):
        sensor = SemsSyncCountSensor(_make_relay(), _make_entry())
        assert sensor.unique_id == "test_entry_id_sync_count"

    async def test_restore_same_day(self, hass: HomeAssistant) -> None:
        """Restore count when stored date matches today."""
        relay = _make_relay()
        relay._sync_count = 0
        relay._sync_count_date = ""

        sensor = SemsSyncCountSensor(relay, _make_entry())
        sensor.hass = hass

        today_str = "2024-06-15"

        mock_data = MagicMock()
        mock_data.native_value = 25
        mock_data.as_dict.return_value = {
            "attributes": {"date": today_str},
        }

        with (
            patch.object(sensor, "async_get_last_sensor_data", return_value=mock_data),
            patch(
                "custom_components.goodwe_local_sems_bridge.sensor.dt_util"
            ) as mock_dt,
        ):
            mock_dt.now.return_value.strftime.return_value = today_str
            await sensor.async_added_to_hass()

        assert relay._sync_count == 25
        assert relay._sync_count_date == today_str

    async def test_restore_different_day(self, hass: HomeAssistant) -> None:
        """Don't restore count when stored date is yesterday."""
        relay = _make_relay()
        relay._sync_count = 0
        relay._sync_count_date = ""

        sensor = SemsSyncCountSensor(relay, _make_entry())
        sensor.hass = hass

        mock_data = MagicMock()
        mock_data.native_value = 25
        mock_data.as_dict.return_value = {
            "attributes": {"date": "2024-06-14"},  # yesterday
        }

        with (
            patch.object(sensor, "async_get_last_sensor_data", return_value=mock_data),
            patch(
                "custom_components.goodwe_local_sems_bridge.sensor.dt_util"
            ) as mock_dt,
        ):
            mock_dt.now.return_value.strftime.return_value = "2024-06-15"
            await sensor.async_added_to_hass()

        assert relay._sync_count == 0  # not restored

    async def test_restore_invalid_value(self, hass: HomeAssistant) -> None:
        """Invalid stored value should be silently ignored."""
        relay = _make_relay()
        relay._sync_count = 0

        sensor = SemsSyncCountSensor(relay, _make_entry())
        sensor.hass = hass

        mock_data = MagicMock()
        mock_data.native_value = "not_a_number"
        mock_data.as_dict.return_value = {
            "attributes": {"date": "2024-06-15"},
        }

        with (
            patch.object(sensor, "async_get_last_sensor_data", return_value=mock_data),
            patch(
                "custom_components.goodwe_local_sems_bridge.sensor.dt_util"
            ) as mock_dt,
        ):
            mock_dt.now.return_value.strftime.return_value = "2024-06-15"
            await sensor.async_added_to_hass()

        assert relay._sync_count == 0  # unchanged

    async def test_restore_no_previous_data(self, hass: HomeAssistant) -> None:
        """No previous data should leave count at 0."""
        relay = _make_relay()
        relay._sync_count = 0

        sensor = SemsSyncCountSensor(relay, _make_entry())
        sensor.hass = hass

        with patch.object(sensor, "async_get_last_sensor_data", return_value=None):
            await sensor.async_added_to_hass()

        assert relay._sync_count == 0
