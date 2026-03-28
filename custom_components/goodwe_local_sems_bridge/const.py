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

# Known device header for GoodWe DT family (e.g. GW25K-MT).
# These 21 bytes are prepended by inverter firmware to every POSTGW plaintext packet.
# They are NOT readable via modbus/the goodwe library — firmware-level only.
# Sourced from MITM captures of a GW25K-MT (SN: REDACTED).
# Other DT models within the same firmware generation use the same bytes.
KNOWN_DT_DEVICE_HEADER_HEX = "067552755704570000000e000001310001759475c5"

# Known POSTGW plaintext tail for GoodWe DT family (bytes 167–239, 73 bytes).
#
# DT inverters (_READ_RUNNING_DATA) only return 73 registers (146 bytes, regs
# 0x7594–0x75DC).  The 240-byte POSTGW plaintext needs 219 bytes of modbus data,
# so the remaining 73 bytes (regs 0x75DD–0x7601) MUST be filled with the correct
# static hardware structure — NOT zeros.
#
# These 73 bytes are a constant pointer/sentinel table written by the inverter
# firmware.  They have been verified identical across every MITM-captured packet
# for this inverter model (GW25K-MT, March 2026).  Sending zeros instead causes
# SEMS to accept the packet (ACK received) and accumulate energy (eDay updates),
# but silently refuses to update the live display (pac / last_refresh_time).
#
# Key non-zero positions (register : value):
#   0x75F0 → 0x75FB  (pointer to data word 1)
#   0x75F1 → 0x75FF  (pointer to data word 2)
#   0x75F7 → 0x7601  (pointer to data word 3)
#   0x75F8 → 0x7602  (pointer to data word 4)
#   0x75FB → 0x9121  (data — lifetime energy accumulator, high word)
#   0x75FC → 0x9122  (data — lifetime energy accumulator, low word)
#   0x75FF → 0xFFFF  (end-of-table sentinel)
#   0x7600 → 0xFFFF  (end-of-table sentinel)
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

