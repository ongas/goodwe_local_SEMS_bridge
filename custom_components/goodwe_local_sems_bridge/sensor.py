"""Sensor entities for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import GoodweLocalSemsRelay


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for this config entry."""
    relay: GoodweLocalSemsRelay = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SemsSyncStatusSensor(relay, entry),
        SemsSyncLastTimeSensor(relay, entry),
        SemsSyncCountSensor(relay, entry),
    ], True)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        model="SEMS Bridge",
    )


class SemsSyncStatusSensor(SensorEntity):
    """Reports current sync status: OK, Failed, or Disabled."""

    _attr_has_entity_name = True
    _attr_name = "Sync Status"
    _attr_icon = "mdi:cloud-check"

    def __init__(self, relay: GoodweLocalSemsRelay, entry: ConfigEntry) -> None:
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_sync_status"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        if self._relay._last_sems_sync is None:
            return "Pending"
        return "Failed" if self._relay._sems_sync_failed else "OK"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._relay._last_error:
            return {"last_error": self._relay._last_error}
        return {}

    @property
    def icon(self) -> str:
        status = self.native_value
        if status == "OK":
            return "mdi:cloud-check"
        if status == "Failed":
            return "mdi:cloud-alert"
        return "mdi:cloud-clock"


class SemsSyncLastTimeSensor(SensorEntity):
    """Reports the timestamp of the last successful sync."""

    _attr_has_entity_name = True
    _attr_name = "Last Sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check"

    def __init__(self, relay: GoodweLocalSemsRelay, entry: ConfigEntry) -> None:
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_last_sync"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        return self._relay._last_sems_sync


class SemsSyncCountSensor(RestoreSensor):
    """Reports the number of successful syncs today. Resets at midnight.\n\n    Restores previous value across restarts only if the date matches today,\n    so a restart never carries yesterday's count into a new day.\n    \"\"\"\n\n    _attr_has_entity_name = True\n    _attr_name = "Sync Count"\n    _attr_icon = "mdi:counter"\n    _attr_native_unit_of_measurement = "syncs"\n\n    def __init__(self, relay: GoodweLocalSemsRelay, entry: ConfigEntry) -> None:\n        self._relay = relay\n        self._attr_unique_id = f"{entry.entry_id}_sync_count"\n        self._attr_device_info = _device_info(entry)\n\n    async def async_added_to_hass(self) -> None:\n        """Restore today's sync count after a restart.\n\n        Only applies the restored value if the stored date matches today,\n        so yesterday's syncs are never carried over.\n        \"\"\"\n        from homeassistant.util import dt as dt_util\n        if (last_state := await self.async_get_last_sensor_data()) is not None:\n            try:\n                stored_date = (last_state.extra_data or {}).get("date", "")\n                today = dt_util.now().strftime("%Y-%m-%d")\n                if stored_date == today:\n                    restored = int(last_state.native_value)\n                    self._relay._sync_count = restored\n                    self._relay._sync_count_date = today\n            except (TypeError, ValueError):\n                pass\n\n    @property\n    def native_value(self) -> int:\n        return self._relay._sync_count\n\n    @property\n    def extra_state_attributes(self) -> dict[str, Any]:\n        return {"date": self._relay._sync_count_date}
