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
CONF_DEVICE_HEADER = "device_header"  # 21-byte fixed header (hex string), set at setup

# 21-byte POSTGW plaintext prefix prepended by DT-family inverter firmware.
# Not readable via Modbus — captured via MITM from a GW25K-MT.
# Other DT models in the same firmware generation use identical bytes.
KNOWN_DT_DEVICE_HEADER_HEX = "067552755704570000000e000001310001759475c5"

# Static 73-byte tail for DT-family POSTGW plaintext (bytes 167–239).
#
# DT inverters return only 146 bytes from _READ_RUNNING_DATA (regs 0x7594–0x75DC).
# The 240-byte plaintext requires 219 bytes of Modbus data, so the remaining
# 73 bytes must be filled with this constant firmware-level pointer/sentinel table.
#
# IMPORTANT: Sending zeros here causes SEMS to ACK the packet and accumulate
# energy (eDay) but silently skip updating the live display (pac / last_refresh_time).
KNOWN_DT_PLAINTEXT_TAIL_HEX = (
    "000000000000000000000000000000000000000000000000"
    "000000000000000000000000000075fb75ff000000000000"
    "0000000076017602000000009121912200000000ffffffff"
    "ff"
)

# SEMS cloud
CONF_DEVICE_ID = "device_id"      # 8-char ASCII inverter ID (from device info)
CONF_DEVICE_SERIAL = "device_serial"  # 8-char ASCII serial (from device info)

DEFAULT_INVERTER_PORT = 8899  # GoodWe default UDP port

