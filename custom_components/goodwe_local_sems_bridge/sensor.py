"""Sensor entities for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    ])


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
        if not self._relay.sync_to_cloud:
            return "Disabled"
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
        if status == "Disabled":
            return "mdi:cloud-off"
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


class SemsSyncCountSensor(SensorEntity):
    """Reports the total number of successful syncs since last restart."""

    _attr_has_entity_name = True
    _attr_name = "Sync Count"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "syncs"
    _attr_state_class = "total_increasing"

    def __init__(self, relay: GoodweLocalSemsRelay, entry: ConfigEntry) -> None:
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_sync_count"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        return self._relay._sync_count
