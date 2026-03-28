"""Constants for the GoodWe Local SEMS Bridge integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "goodwe_local_sems_bridge"

PLATFORMS = [Platform.SENSOR]

SEMS_SYNC_INTERVAL = timedelta(minutes=1)  # Sync once per minute

# Inverter connection
CONF_INVERTER_HOST = "inverter_host"
CONF_INVERTER_PORT = "inverter_port"
CONF_MODEL_FAMILY = "model_family"
CONF_DEVICE_HEADER = "device_header"  # 21-byte fixed header (hex string), learned at setup

# SEMS cloud
CONF_DEVICE_ID = "device_id"      # 8-char ASCII inverter ID (from device info)
CONF_DEVICE_SERIAL = "device_serial"  # 8-char ASCII serial (from device info)

DEFAULT_INVERTER_PORT = 8899  # GoodWe default UDP port

