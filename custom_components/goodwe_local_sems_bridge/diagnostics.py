"""Diagnostics for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_GOODWE_ENTRY_ID, CONF_SEMS_STATION_ID, CONF_SEMS_USERNAME, DOMAIN
from .coordinator import GoodweLocalSemsRelay

_TO_REDACT = {CONF_SEMS_USERNAME, CONF_SEMS_STATION_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    relay: GoodweLocalSemsRelay = hass.data[DOMAIN][entry.entry_id]

    data: dict[str, Any] = {
        "config": {
            "goodwe_entry_id": entry.data.get(CONF_GOODWE_ENTRY_ID),
            "sems_station_id": entry.data.get(CONF_SEMS_STATION_ID),
            "sync_to_cloud": entry.data.get("sync_to_cloud", True),
        },
        "sync_status": {
            "last_sync": relay._last_sems_sync.isoformat() if relay._last_sems_sync else None,
            "sync_failed": relay._sems_sync_failed,
            "api_initialized": relay._sems_api is not None,
        },
    }
    return async_redact_data(data, _TO_REDACT)
