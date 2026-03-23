"""The GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_GOODWE_ENTRY_ID,
    CONF_SEMS_STATION_ID,
    DOMAIN,
    PLATFORMS,
    SEMS_SYNC_INTERVAL,
)
from .coordinator import GoodweLocalSemsRelay

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the GoodWe Local SEMS Bridge integration from a config entry."""
    
    hass.data.setdefault(DOMAIN, {})

    # Verify that the Goodwe integration exists
    goodwe_entry_id = entry.data.get(CONF_GOODWE_ENTRY_ID)
    goodwe_entries = hass.config_entries.async_entries("goodwe")
    
    if not any(e.entry_id == goodwe_entry_id for e in goodwe_entries):
        raise ConfigEntryNotReady("Goodwe integration not found")

    # Create the relay
    relay = GoodweLocalSemsRelay(
        hass=hass,
        goodwe_entry_id=goodwe_entry_id,
        sems_username=entry.data.get(CONF_USERNAME),
        sems_password=entry.data.get(CONF_PASSWORD),
        sems_station_id=entry.data.get(CONF_SEMS_STATION_ID),
    )

    # Test initial sync
    if not await relay.async_sync():
        _LOGGER.warning("Initial SEMS sync failed, but entry will continue to retry")

    # Set up periodic syncing (once per minute)
    async def sync_callback(now):
        """Sync data to SEMS periodically."""
        await relay.async_sync()

    # Store the relay and remove callback function
    hass.data[DOMAIN][entry.entry_id] = relay
    remove_listener = async_track_time_interval(
        hass, sync_callback, SEMS_SYNC_INTERVAL
    )
    hass.data[DOMAIN][f"{entry.entry_id}_listener"] = remove_listener

    # Set up platforms (should be empty list, but Home Assistant expects it)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Remove the periodic sync listener
    remove_listener = hass.data[DOMAIN].pop(f"{entry.entry_id}_listener", None)
    if remove_listener:
        remove_listener()

    # Remove the relay
    hass.data[DOMAIN].pop(entry.entry_id, None)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    return unload_ok

