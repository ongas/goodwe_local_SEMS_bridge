# GoodWe Local SEMS Bridge

A Home Assistant custom integration that reads live inverter data directly via local Modbus and relays it to the GoodWe SEMS cloud using the native POSTGW protocol — keeping your SEMS dashboard updated without relying on the inverter's WiFi module to phone home.

## Why This Exists

GoodWe inverters communicate with SEMS using a proprietary encrypted TCP protocol called **POSTGW**. When you use the local Modbus integration (port 8899) instead of the inverter's built-in cloud path, SEMS stops receiving updates and your dashboard goes stale.

This integration bridges the gap:
1. Reads live sensor data directly from the inverter via Modbus
2. Builds and encrypts a valid POSTGW packet
3. Sends it to `tcp.goodwe-power.com:20001` every 60 seconds

No SEMS credentials required — the integration authenticates using the inverter's own serial number, exactly as the inverter firmware does.

## Protocol Reference

The POSTGW protocol was reverse-engineered via MITM capture of a GW25K-MT (DT family). Below are the key findings.

### Packet Structure (294 bytes)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 6 | Magic: `POSTGW` |
| 6 | 4 | Length: `281` (uint32 BE) |
| 10 | 2 | Type: `0x0104` (uint16 BE) |
| 12 | 2 | Padding: `0x0000` |
| 14 | 8 | Device ID (bytes 0–7 of serial number, ASCII) |
| 22 | 8 | Device Serial (bytes 8–15 of serial number, ASCII) |
| 30 | 16 | IV: 6-byte local timestamp + 10 zero bytes |
| 46 | 6 | Envelope timestamp (same 6 bytes) |
| 52 | 240 | Ciphertext (AES-128-CBC) |
| 292 | 2 | CRC-16 Modbus over bytes 0–291 (uint16 BE) |

### Encryption

- **Algorithm**: AES-128-CBC
- **Key**: `0xFF × 16` (all 255s — hardcoded in GoodWe firmware)
- **IV**: `timestamp_bytes(6) + zeros(10)`

### Plaintext Layout (240 bytes)

| Offset | Size | Content |
|--------|------|---------|
| 0x00 | 21 | Device header (firmware constant, not readable via Modbus) |
| 0x15 | 6 | Timestamp (YY MM DD HH mm ss, inverter local time) |
| 0x1B | 213 | Modbus data: 146 bytes live registers + 73 bytes static tail |

### Key Findings

**Device header (21 bytes):** A firmware-level constant prepended to every packet by the inverter firmware — not accessible via the Modbus/goodwe library. The DT-family constant is embedded in this integration and applied automatically. The config flow captures it during setup so other families can be supported.

**Static tail bytes (73 bytes):** DT inverters return only 146 bytes (73 registers) from `_READ_RUNNING_DATA`. The POSTGW plaintext requires 219 bytes of Modbus data. The remaining 73 bytes are a static pointer/sentinel table written by the inverter firmware. **Sending zeros here causes SEMS to ACK the packet and accumulate `eDay` but silently skip updating the live display (`pac` / `last_refresh_time`).** The correct bytes are embedded in this integration.

**Persistent TCP connection:** SEMS only updates the live `pac` and `last_refresh_time` display while the TCP connection to `tcp.goodwe-power.com:20001` remains open. Opening a new connection per packet causes SEMS to accept packets but not refresh the live status. This integration maintains a persistent connection with automatic reconnection on EOF.

**Energy plausibility check:** SEMS performs a server-side sanity check — if `e_day` in a new packet is lower than what SEMS already holds for that day, it ACKs the packet but skips the live display update. Under normal operation `e_day` only increases throughout the day, so this is not an issue. It can become apparent after testing with synthetic data from a different day.

**SEMS ACK format (58 bytes, header `GW`):**
- `[24:40]` — IV (server timestamp + 10 zeros)
- `[40:56]` — AES-128-CBC payload: all-zeros = ACK, `0x02`+zeros = NACK

## Installation

### Via HACS (recommended)

1. Open Home Assistant → HACS → Integrations
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/ongas/goodwe_local_SEMS_bridge`, category: **Integration**
4. Search for **GoodWe Local SEMS Bridge** and install
5. Restart Home Assistant

### Manual

Copy `custom_components/goodwe_local_sems_bridge/` into your HA `custom_components/` directory and restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **GoodWe Local SEMS Bridge**
3. Enter your inverter's local IP address (port defaults to `8899`)
4. The integration connects to the inverter, auto-detects the model and serial number, and applies the correct device header
5. Confirm to create the entry

## Sensors

| Sensor | Description |
|--------|-------------|
| Sync Status | `OK`, `Failed`, or `Pending` |
| Last Sync | Timestamp of the last successful sync |
| Sync Count | Number of successful syncs today (resets at midnight, survives restarts) |

## Debug Logging

```yaml
logger:
  logs:
    custom_components.goodwe_local_sems_bridge: debug
```

## Troubleshooting

### SEMS live display not updating

Check HA logs for `POSTGW packet sent` lines. If packets are sending but SEMS is not updating:

- **Energy plausibility**: If testing earlier today injected a higher `e_day` value into SEMS, it will ignore updates until the live value naturally exceeds it. Wait it out — no fix required.
- **Network**: Ensure outbound TCP to `tcp.goodwe-power.com:20001` is allowed through your firewall.
- **NACK response**: Look for `SEMS returned NACK` warnings in the log. This indicates a malformed packet.

### Inverter unreachable at startup

If the inverter is in overnight standby (no solar generation), the integration logs a warning and retries every 60 seconds. It reconnects automatically when the inverter wakes up.

### Compatibility

Tested on GoodWe **DT family** (e.g. GW25K-MT). Other GoodWe families using the POSTGW protocol should work but may have different register counts or device headers. Open an issue if your model needs adjustments.

## Requirements

- Home Assistant 2024.1.0 or later
- GoodWe inverter reachable on local network (port 8899)
- Outbound TCP to `tcp.goodwe-power.com:20001`
- Python packages: `cryptography>=41.0.0`, `goodwe>=0.3.0`

## License

See [LICENSE](LICENSE)

## Contributing

Issues and pull requests welcome: https://github.com/ongas/goodwe_local_SEMS_bridge/issues

---
*Not affiliated with GoodWe. Protocol details discovered through independent research.*
