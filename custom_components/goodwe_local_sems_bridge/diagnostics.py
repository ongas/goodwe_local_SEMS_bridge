"""Diagnostics for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_HEADER, CONF_DEVICE_ID, CONF_DEVICE_SERIAL, CONF_INVERTER_HOST, DOMAIN
from .coordinator import GoodweLocalSemsRelay

_TO_REDACT = {CONF_INVERTER_HOST, CONF_DEVICE_ID, CONF_DEVICE_SERIAL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    relay: GoodweLocalSemsRelay = hass.data[DOMAIN][entry.entry_id]

    data: dict[str, Any] = {
        "config": {
            "inverter_host": entry.data.get(CONF_INVERTER_HOST),
            "device_id": entry.data.get(CONF_DEVICE_ID),
            "device_serial": entry.data.get(CONF_DEVICE_SERIAL),
            "device_header": entry.data.get(CONF_DEVICE_HEADER),
        },
        "sync_status": {
            "last_sync": relay._last_sems_sync.isoformat() if relay._last_sems_sync else None,
            "sync_count": relay._sync_count,
            "last_sync_failed": relay._sems_sync_failed,
            "last_error": relay._last_error,
        },
        "runtime_data_keys": list(relay.last_runtime_data.keys()),
    }
    return async_redact_data(data, _TO_REDACT)
