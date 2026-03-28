"""The GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_DEVICE_HEADER,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_INVERTER_HOST,
    CONF_INVERTER_PORT,
    CONF_MODEL_FAMILY,
    DEFAULT_INVERTER_PORT,
    DOMAIN,
    PLATFORMS,
    SEMS_SYNC_INTERVAL,
)
from .coordinator import GoodweLocalSemsRelay

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the GoodWe Local SEMS Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    relay = GoodweLocalSemsRelay(
        hass=hass,
        inverter_host=entry.data[CONF_INVERTER_HOST],
        inverter_port=entry.data.get(CONF_INVERTER_PORT, DEFAULT_INVERTER_PORT),
        model_family=entry.data.get(CONF_MODEL_FAMILY, "None"),
        device_header_hex=entry.data[CONF_DEVICE_HEADER],
        device_id=entry.data[CONF_DEVICE_ID],
        device_serial=entry.data[CONF_DEVICE_SERIAL],
    )

    # Initial connection check
    if not await relay.async_connect():
        raise ConfigEntryNotReady(
            f"Cannot connect to inverter at {entry.data[CONF_INVERTER_HOST]}"
        )

    # Attempt first sync immediately
    await relay.async_sync()

    async def _sync_callback(now):
        await relay.async_sync()

    hass.data[DOMAIN][entry.entry_id] = relay
    hass.data[DOMAIN][f"{entry.entry_id}_listener"] = async_track_time_interval(
        hass, _sync_callback, SEMS_SYNC_INTERVAL
    )

    _LOGGER.info(
        "GoodWe Local SEMS Bridge started for %s — syncing to SEMS every 60 seconds",
        entry.data[CONF_INVERTER_HOST],
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    listener = hass.data[DOMAIN].pop(f"{entry.entry_id}_listener", None)
    if listener:
        listener()

    hass.data[DOMAIN].pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
