# GoodWe Local SEMS Bridge

A Home Assistant custom integration that syncs GoodWe inverter data from the official local modbus integration to the Goodwe SEMS API.

## Features

- 📡 **Local Data Source**: Reads real-time data from your GoodWe inverter via modbus (official integration)
- ☁️ **Cloud Sync**: Automatically syncs inverter data to Goodwe's SEMS cloud API
- ⚡ **Efficient**: Syncs once per minute, non-blocking operation
- 🔄 **Reliable**: SEMS sync failures never affect local operation
- 🛡️ **Safe**: Acts as a simple relay/bridge, doesn't modify official integration behavior

## Problem It Solves

This integration solves a common issue with GoodWe inverters:
- The **official modbus integration** reads data locally (fast, no cloud latency) but stops the SEMS API from being updated
- The **SEMS API integration** keeps the cloud updated but is slow and laggy
- This bridge uses the fast local reads and **also** keeps SEMS updated

## Requirements

- Home Assistant with the **official GoodWe inverter** integration already configured
- SEMS API account with your Goodwe inverter registered

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

1. In Home Assistant, go to **Settings → Devices & Services → Create Automation**
2. Click **"Create Integration"**
3. Search for **"GoodWe Local SEMS Bridge"**
4. Select your configured GoodWe integration
5. Enter your SEMS API credentials:
   - SEMS Username
   - SEMS Password
   - SEMS Station ID

## How It Works

1. **Initial Setup**: Bridge verifies SEMS credentials are valid
2. **Continuous Operation**: Every 60 seconds, the bridge:
   - Reads the latest data from your configured GoodWe modbus integration
   - Syncs that data to your Goodwe SEMS cloud account
3. **Error Handling**: If SEMS sync fails, it logs the error but continues operating

## Configuration

The integration requires **no additional configuration** after setup. It automatically:
- Syncs once per minute (fixed, non-configurable)
- Uses the data frequency from your official GoodWe integration configuration
- Gracefully handles SEMS API failures

## Troubleshooting

### "No GoodWe integration found"
- Ensure you have the **official GoodWe inverter** integration installed and configured
- Install it from: Settings → Devices & Services → Create Integration → Search "GoodWe Inverter"

### "Failed to authenticate with SEMS API"
- Verify your SEMS username and password
- Verify your SEMS Station ID is correct
- Check that your account is active on SEMS portal

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
