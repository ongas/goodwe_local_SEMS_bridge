# GoodWe Local SEMS Bridge

A Home Assistant custom integration that syncs GoodWe inverter data, using a local Modbus connection, to Goodwe SEMS.

## Features

- 📡 **Local Data Source**: Reads real-time data from your GoodWe inverter via modbus
- ☁️ **Cloud Sync**: Automatically syncs inverter data to Goodwe's SEMS cloud
- ⚡ **Efficient**: Syncs once per minute, non-blocking operation
- 🔄 **Reliable**: SEMS sync failures never affect local operation
- 🛡️ **Safe**: Acts as a simple relay/bridge, doesn't modify any behavior in HA

## Problem It Solves

This integration solves a common issue with GoodWe inverters:
- The **official modbus integration** reads data locally (fast, no cloud latency) but stops SEMS from being updated
- The **SEMS API integration** retrieves from cloud but laggy due to lengthy round-trips, preventing it from being used for near-real-time integration requirements
- This bridge allows you to keep your local Modbus integration (eg the official GoodWe HA Integration), and keeps SEMS updated.


## Requirements

- Your Inverter Device S/N

## Installation

### Via HACS (Easy)
1. Open Home Assistant → HACS
2. Click on "Custom repositories"
3. Add: `https://github.com/ongas/goodwe_local_SEMS_bridge`
4. Select "Integration"
5. Search for "GoodWe Local SEMS Bridge" and install

### Manual
1. Copy the `custom_components/goodwe_local_sems_bridge` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. In Home Assistant, go to **Settings → Devices & Services → Create Integration**
2. Click **"Create Integration"** (or search for "GoodWe Local SEMS Bridge")
3. Select your configured GoodWe integration
4. Enter your SEMS API credentials:
   - SEMS Username
   - SEMS Password
   - SEMS Station ID
5. Choose whether to enable cloud sync:
   - **Sync to Goodwe Cloud**: Enable/disable syncing to SEMS (default: enabled)

## How It Works

1. **Initial Setup**: Bridge verifies SEMS credentials are valid (if cloud sync enabled)
2. **Continuous Operation**: 
   - Reads the latest data from your configured GoodWe modbus integration
   - If cloud sync is enabled: Syncs data to Goodwe SEMS cloud every 60 seconds (factory default)
   - If cloud sync is disabled: Data is only available locally
3. **Error Handling**: If SEMS sync fails, it logs the error but continues operating

## Configuration

The integration is configured through the setup wizard and no additional manual configuration is needed:

- **Sync to Goodwe Cloud** (default: enabled): 
  - When enabled, data is automatically synced to SEMS every 60 seconds (factory default frequency)
  - When disabled, no cloud sync occurs
  - The bridge reads data from your official GoodWe integration at its configured frequency and syncs to cloud on the 60-second schedule

## Troubleshooting

### SEMS sync failures in logs
- These are non-fatal and don't affect local operation
- The integration will keep retrying every minute
- Check your internet connection and SEMS API status

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## Disclaimer

This is a community-created integration. It is not officially affiliated with GoodWe.
