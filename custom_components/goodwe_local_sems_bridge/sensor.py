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
        InverterConnectionStatusSensor(relay, entry),
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


class InverterConnectionStatusSensor(SensorEntity):
    """Reports connection status to the inverter: Connected or Disconnected."""

    _attr_has_entity_name = True
    _attr_name = "Connection Status"
    _attr_icon = "mdi:connection"

    def __init__(self, relay: GoodweLocalSemsRelay, entry: ConfigEntry) -> None:
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_connection_status"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        return "Connected" if self._relay._inverter is not None else "Disconnected"

    @property
    def icon(self) -> str:
        return "mdi:check-circle" if self.native_value == "Connected" else "mdi:alert-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {}
        if self._relay._inverter is not None:
            attrs["model"] = self._relay._inverter.model_name
            attrs["serial"] = self._relay._inverter.serial_number
        if self._relay._last_error:
            attrs["last_error"] = self._relay._last_error
        return attrs


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
    """Reports the number of successful syncs today. Resets at midnight.

    Restores previous value across restarts only if the date matches today,
    so a restart never carries yesterday's count into a new day.
    """

    _attr_has_entity_name = True
    _attr_name = "Sync Count"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "syncs"

    def __init__(self, relay, entry):
        self._relay = relay
        self._attr_unique_id = f"{entry.entry_id}_sync_count"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self):
        if (last_state := await self.async_get_last_sensor_data()) is not None:
            try:
                extra = last_state.as_dict().get("attributes", {})
                stored_date = extra.get("date", "")
                today = dt_util.now().strftime("%Y-%m-%d")
                if stored_date == today:
                    restored = int(last_state.native_value)
                    self._relay._sync_count = restored
                    self._relay._sync_count_date = today
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self):
        return self._relay._sync_count

    @property
    def extra_state_attributes(self):
        return {"date": self._relay._sync_count_date}
