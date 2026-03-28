"""Diagnostics for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import GoodweLocalSemsRelay


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    relay: GoodweLocalSemsRelay = hass.data[DOMAIN][entry.entry_id]

    goodwe_entry = hass.config_entries.async_get_entry(relay.goodwe_entry_id)
    goodwe_state = str(goodwe_entry.state) if goodwe_entry else "not found"

    try:
        goodwe_data = await relay._get_goodwe_data()
    except Exception:  # pylint: disable=broad-except
        goodwe_data = None

    return {
        "config": {
            "goodwe_entry_id": relay.goodwe_entry_id,
            "goodwe_entry_state": goodwe_state,
            "sems_station_id": relay.sems_station_id,
            "device_id": relay.device_id,
            "device_serial": relay.device_serial,
            "sync_to_cloud": relay.sync_to_cloud,
        },
        "sync_status": {
            "last_sync": relay._last_sems_sync.isoformat() if relay._last_sems_sync else None,
            "sync_count": relay._sync_count,
            "last_sync_failed": relay._sems_sync_failed,
            "last_error": relay._last_error,
        },
        "goodwe_data_available": goodwe_data is not None,
        "goodwe_data_keys": list(goodwe_data.keys()) if goodwe_data else [],
    }
