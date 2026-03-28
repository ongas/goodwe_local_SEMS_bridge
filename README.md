# GoodWe Local SEMS Bridge

A Home Assistant custom integration that relays real-time GoodWe inverter data to the Goodwe SEMS cloud API using the POSTGW protocol. Reads data from the official GoodWe modbus integration and sends it to SEMS, keeping your cloud dashboard up-to-date with live local data.

## Features

- 📊 **Real-Time Cloud Sync**: Sends live inverter data from local modbus to SEMS cloud
- ☁️ **POSTGW Protocol**: Uses GoodWe's native POSTGW/AA55 protocol for reliable communication  
- 🔐 **AES-128-CBC Encryption**: Properly encrypted packets with CRC-16 Modbus validation
- ⚡ **60-Second Intervals**: Syncs to SEMS at factory-default intervals (configurable)
- 🔄 **Reliable**: Failures don't affect local Home Assistant operation
- 🛡️ **Non-Invasive**: Works perfectly alongside the official GoodWe integration

## Problem It Solves

Standard GoodWe inverters present a dilemma:

- **Official modbus integration**: Fast local reads via port 8899, but doesn't update SEMS cloud
- **SEMS cloud API**: Keeps cloud updated, but is slow and returns stale data
- **This bridge**: Reads live local data and sends it to SEMS, so your cloud dashboard stays current

## How It Works

### Architecture

```
┌──────────────────┐
│ GoodWe Inverter  │
│ (WiFi Module)    │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────┐
│ Official HA GoodWe Integ.    │
│ (Modbus 8899 - Real-Time)    │
└────────┬─────────────────────┘
         │ Reads latest data every 60s
         ▼
┌──────────────────────────────┐
│ SEMS Bridge (This Plugin)    │
│                              │
│ • Read data from GoodWe      │
│ • Build POSTGW packet        │
│ • AES-128-CBC encrypt        │
│ • CRC-16 Modbus checksum     │
│ • Send to SEMS               │
└────────┬─────────────────────┘
         │ TCP to 3.105.0.175:20001
         ▼
┌──────────────────────────────┐
│ Goodwe SEMS Cloud            │
│ (Dashboard Updated!)         │
└──────────────────────────────┘
```

### POSTGW Protocol (Implemented)

The bridge builds valid POSTGW packets with:

**Packet Structure (294 bytes total):**
- Header: `POSTGW` (6 bytes)
- Length: 281 (4 bytes, big-endian) ⚠️ **Critical: Not 282!**
- Type: 0x0104 (2 bytes, big-endian) - Data packet type
- Envelope: 40 bytes of plaintext metadata
- Ciphertext: 240 bytes AES-128-CBC encrypted modbus registers
- CRC: CRC-16 Modbus (2 bytes, big-endian)

**Encryption:**
- Algorithm: AES-128-CBC  
- Key: `0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF` (16 bytes, all 255s)
- IV: 16 bytes (regenerated per packet)

**CRC Calculation:**
- Algorithm: CRC-16 Modbus (polynomial 0xA001)
- Reflected input and output
- Computed over entire packet

## Installation

### 1. Install the Integration

Copy the `goodwe_local_sems_bridge` directory to `~/.homeassistant/custom_components/`

```bash
# Directory structure:
~/.homeassistant/custom_components/goodwe_local_sems_bridge/
├── __init__.py
├── config_flow.py
├── const.py
├── coordinator.py
├── manifest.json
├── modbus_unpacker.py
├── strings.json
└── translations/
    └── en.json
```

### 2. Restart Home Assistant

In Home Assistant UI:
- Go to **Settings** > **Developer Tools** > **YAML**
- Or restart Home Assistant service

### 3. Add Integration

- Go to **Settings** > **Devices & Services** > **Create Automation** > **Integrations**
- Search for "GoodWe Local SEMS Bridge"
- Click **Create**

## Configuration

### Step 1: Select GoodWe Integration
Select which GoodWe inverter integration to sync from (must already be configured)

### Step 2: SEMS Credentials
Enter your SEMS account credentials:
- **SEMS Username**: Your SEMS login email
- **SEMS Password**: Your SEMS account password  
- **SEMS Station ID**: Your inverter's station ID (visible in SEMS dashboard)

<details>
<summary>Don't know your Station ID?</summary>

1. Log in to [SEMS](https://www.goodwe-power.com)
2. Navigate to your inverter dashboard
3. Check the URL or device settings for the station/device ID
</details>

### Step 3: Cloud Sync Settings
- **Sync to Goodwe Cloud SEMS**: Enable to send data every 60 seconds  
  - Enabled (recommended): Keep SEMS dashboard updated automatically
  - Disabled: Component still works but won't update SEMS

### Step 4: AA55 MITM Proxy (Advanced)
- **Enable AA55 MITM Proxy**: Leave disabled for now  
  - This would intercept inverter packets directly (future enhancement)
- **AA55 Proxy Port**: If enabled, TCP port to listen on (default: 20001)

## Usage

### Monitor Sync Status

Check if data is being sent to SEMS:

1. In Home Assistant, go to **Settings** > **Devices & Services** > **GoodWe Local SEMS Bridge**
2. View the integration details
3. Last sync timestamp confirms data transmission

### Verify in SEMS

1. Log in to [SEMS](https://www.goodwe-power.com)
2. Navigate to your inverter dashboard
3. Verify real-time power output and other metrics are updating

### Check Logs

View Home Assistant logs for sync status:

```yaml
logger:
  logs:
    custom_components.goodwe_local_sems_bridge: debug
```

Then check **Settings** > **System** > **Logs** for messages like:
- "POSTGW packet sent to SEMS successfully"
- "Failed to send POSTGW packet to SEMS"
- "No data from Goodwe coordinator yet"

## Troubleshooting

### "GoodWe integration not found"
- Ensure you have the official GoodWe integration installed and configured
- Go to **Settings** > **Devices & Services** to verify

### "No data from Goodwe coordinator yet"
- Wait a few minutes for the official integration to collect initial data
- Check that your inverter is online and communicating with the WiFi module

### Nothing updating in SEMS
1. Verify cloud sync is **enabled** in configuration
2. Check Home Assistant logs for errors:
   ```yaml
   logger:
     logs:
       custom_components.goodwe_local_sems_bridge.coordinator: debug
   ```
3. Confirm SEMS credentials are correct
4. Verify network connectivity to SEMS (3.105.0.175:20001)

### Connection timeout to SEMS
- Check firewall rules; ensure outbound TCP to 3.105.0.175:20001 is allowed
- Verify your internet connection is stable
- Try temporarily disabling VPN/proxy if one is in use

## Technical Details

### Data Mapping

The component maps GoodWe modbus registers to POSTGW payload:

| Register | Offset | Data | Description |
|----------|--------|------|-------------|
| VPVX | 0-30 | uint16 | PV String X Voltage (÷100) |
| IPVX | 2-32 | uint16 | PV String X Current (÷100) |
| PPVX | 4-60 | uint32 | PV String X Power |
| VGRIDX | 32-60 | uint16 | Grid Phase X Voltage (÷100) |
| IGRIDX | 34-62 | uint16 | Grid Phase X Current (÷100) |
| FGRIDX | 36-64 | uint16 | Grid Frequency (÷100) |
| PGRIDX | 40-68 | sint16 | Grid Phase X Power |
| P_TOTAL | 68 | uint32 | Total Power Output |
| TEMP | 80 | sint16 | Heatsink Temperature (°C) |
| E_TOTAL | 90 | uint32 | Total Energy (÷10 kWh) |

### Packet Validation

Every POSTGW packet includes:

1. **Length Field Validation** (critical bug fix)
   - Correct: `2 + 40 + 240 - 1 = 281` ✅
   - Wrong: `40 + 240 + 2 = 282` ❌

2. **CRC-16 Modbus Validation**  
   - Polynomial: 0xA001
   - Verified against real GoodWe packets
   - Ensures SEMS accepts the packet

3. **AES Encryption**
   - Each payload is encrypted independently
   - IV randomly generated per packet
   - Key is hardcoded in GoodWe firmware

### Sync Interval

- Default: 60 seconds (factory default for POSTGW)
- Configured in: `const.py` → `SEMS_SYNC_INTERVAL`
- Modifiable by creating a custom configuration

## API Reference

### Service: goodwe_local_sems_bridge.sync_now

Manually trigger a sync to SEMS (in development):

```yaml
service: goodwe_local_sems_bridge.sync_now
data:
  entity_id: goodwe_local_sems_bridge.myinverter
```

## Advanced Configuration

### Custom Sync Interval

To change sync interval, modify `const.py`:

```python
# From: SEMS_SYNC_INTERVAL = timedelta(minutes=1)
# To:   SEMS_SYNC_INTERVAL = timedelta(seconds=30)
```

### Enable Debug Logging

```yaml
logger:
  logs:
    custom_components.goodwe_local_sems_bridge.coordinator: debug
    custom_components.goodwe_local_sems_bridge.config_flow: debug
```

## Known Limitations

- ⚠️ MITM proxy not yet implemented (AA55 local packet interception)
- Data mapping covers common ET series inverter registers (expand for other models)
- POSTGW IV regeneration (could be optimized to preserve inverter's IV)

## Contributing

Issues and feature requests: https://github.com/ongas/goodwe_local_SEMS_bridge/issues

## License

See [LICENSE](LICENSE) file

## Installation

### Via HACS (Easy)
1. Open Home Assistant → HACS
2. Click on "Custom repositories"
3. Add: `https://github.com/ongas/goodwe_local_SEMS_bridge`
4. Select "Integration"
5. Search for "GoodWe Local SEMS Bridge" and install

### Manual
1. Copy `custom_components/goodwe_local_sems_bridge` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. In Home Assistant, go to **Settings → Devices & Services → Create Integration**
2. Search for "GoodWe Local SEMS Bridge"
3. **Step 1 - Select Integration**: Choose your configured GoodWe integration
4. **Step 2 - SEMS Credentials** (optional for cloud sync):
   - SEMS Username
   - SEMS Password  
   - SEMS Station ID
5. **Step 3 - Sync Settings**:
   - **Sync to Goodwe Cloud**: Enable/disable sending data to SEMS (default: disabled)
6. **Step 4 - AA55 Proxy Settings**:
   - **Enable AA55 Proxy**: Toggle local MITM proxy (default: disabled)
   - **Proxy Port**: Port to listen on (default: 20001)

### Configure Inverter WiFi Module

After setup, configure your inverter's WiFi module to send data to the bridge:

```
Inverter WiFi Settings:
├─ Server Host: <Your Home Assistant IP>
├─ Server Port: 20001 (or custom port configured above)
└─ Protocol: AA55/POSTGW (automatic)
```

(Exact steps depend on your inverter model - refer to inverter documentation)

## Current Status

**✅ Working:**
- AA55 protocol MITM proxy listening and packet interception
- AA55 packet parsing, decryption, and CRC validation
- Integration with official GoodWe coordinator
- Configuration UI for proxy settings

**🚧 In Development:**
- Payload building from latest inverter sensor values
- Modbus register mapping (data field → binary format conversion)
- Testing and validation of sent packets

**⏸️ Disabled for Safety:**
- SEMS cloud relay (will enable once payload is verified correct)
- To prevent sending incomplete/incorrect data to cloud

## Modbus Data Mapping

The bridge maps GoodWe inverter sensors to AA55 protocol Modbus registers:

| Category | Example Fields |
|----------|---|
| **Power** | Power output (W), PV power, grid power, battery power |
| **Energy** | Daily yield (kWh), total yield, export/import energy |
| **Voltage** | Grid phase voltages, PV voltage, battery voltage |
| **Current** | Grid current, PV current, battery charge/discharge current |
| **Status** | Operating mode, error codes, battery SOC |
| **Temperature** | Radiator, module, battery, ambient temperatures |

(Complete register mappings documented in `/mnt/e/GOODWE_MODBUS_REGISTER_MAPPINGS.md`)

## Troubleshooting

### "No GoodWe integration found"
- Ensure you have the **official GoodWe inverter** integration installed
- Install from: Settings → Devices & Services → Create Integration → Search "GoodWe Inverter"

### AA55 Proxy won't start
- Check if port 20001 is already in use: `netstat -an | grep 20001`
- Try using a different port in configuration
- Ensure Home Assistant has permission to bind to the port

### Inverter not connecting to proxy
- Verify inverter WiFi module can reach Home Assistant IP on configured port
- Check Home Assistant firewall rules
- Verify inverter configuration is correct (IP, port, protocol)

### Data not syncing to SEMS
- SEMS relay is currently disabled by default (safety feature)
- Payload mapping is still being finalized
- Check logs for packet processing errors

## Notes

- **MITM proxy is optional** - you can use this with official integration alone (proxy disabled)
- **Non-invasive** - doesn't interfere with official modbus integration
- **Safe defaults** - SEMS relay disabled, won't send data without explicit configuration
- **In active development** - payload mapping and testing ongoing

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## Disclaimer

This is a community-created integration. It is not officially affiliated with GoodWe.
