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
    CONF_SYNC_TO_CLOUD,
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
    
    goodwe_entry = next(
        (e for e in goodwe_entries if e.entry_id == goodwe_entry_id),
        None,
    )
    
    if not goodwe_entry:
        # If no goodwe integration exists at all, provide helpful error message
        if not goodwe_entries:
            _LOGGER.error(
                "GoodWe Local SEMS Bridge setup failed: The GoodWe integration is not configured. "
                "Please install and configure the GoodWe integration first."
            )
        else:
            _LOGGER.error(
                "GoodWe Local SEMS Bridge setup failed: The configured GoodWe integration (ID: %s) "
                "is no longer available. Please reconfigure this integration.",
                goodwe_entry_id,
            )
        raise ConfigEntryNotReady("GoodWe integration not found or not yet loaded")

    # Create the relay
    sync_to_cloud = entry.data.get(CONF_SYNC_TO_CLOUD, True)
    relay = GoodweLocalSemsRelay(
        hass=hass,
        goodwe_entry_id=goodwe_entry_id,
        sems_username=entry.data.get(CONF_USERNAME),
        sems_password=entry.data.get(CONF_PASSWORD),
        sems_station_id=entry.data.get(CONF_SEMS_STATION_ID),
        sync_to_cloud=sync_to_cloud,
    )

    # Test initial sync if cloud sync is enabled
    if sync_to_cloud:
        if not await relay.async_sync():
            _LOGGER.warning("Initial SEMS sync failed, but entry will continue to retry")
    
    # Set up periodic cloud syncing (once per minute) if enabled
    async def sync_callback(now):
        """Sync data to SEMS periodically."""
        await relay.async_sync()

    # Store the relay
    hass.data[DOMAIN][entry.entry_id] = relay
    
    # Only set up cloud sync listener if sync to cloud is enabled
    if sync_to_cloud:
        remove_listener = async_track_time_interval(
            hass, sync_callback, SEMS_SYNC_INTERVAL
        )
        hass.data[DOMAIN][f"{entry.entry_id}_listener"] = remove_listener
        _LOGGER.info(
            "GoodWe Local SEMS Bridge configured: cloud_sync=enabled (60s factory default)"
        )
    else:
        hass.data[DOMAIN][f"{entry.entry_id}_listener"] = None
        _LOGGER.info(
            "GoodWe Local SEMS Bridge configured: cloud_sync=disabled"
        )

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

