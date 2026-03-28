"""Constants for the GoodWe Local SEMS Bridge integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "goodwe_local_sems_bridge"

PLATFORMS = []  # No entities exposed - just relay functionality

SEMS_SYNC_INTERVAL = timedelta(minutes=1)  # Factory default: sync once per minute

CONF_GOODWE_ENTRY_ID = "goodwe_entry_id"
CONF_SEMS_USERNAME = "sems_username"
CONF_SEMS_PASSWORD = "sems_password"
CONF_SEMS_STATION_ID = "sems_station_id"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_SERIAL = "device_serial"
CONF_SYNC_TO_CLOUD = "sync_to_cloud"

