# v0.5.2 - Fix reconnect loop under UDP contention

## What's New
- **Fixed vicious reconnect loop**: v0.5.1's failure counter was never reset when forcing a reconnect, so the fresh connection's very first read failure would trigger another immediate disconnect — creating an infinite reconnect loop under contention.
- **Raised reconnect threshold from 3 to 10**: Each reconnect adds a UDP discovery probe to an already congested bus, making contention worse. Now tolerates 10 consecutive failures (~10 minutes at 1-min sync interval, ~30 internal UDP attempts) before assuming the inverter is truly offline.

---

# v0.5.1 - Fix UDP contention with official GoodWe integration

## What's New
- **Resilient inverter connection**: The inverter object is now kept alive across transient read failures instead of forcing an expensive full reconnect (4+ UDP calls) on every failure. Only resets after 3 consecutive failures. This dramatically reduces UDP contention when the official GoodWe integration is polling the same inverter at 500ms intervals.
- **SEMS failures no longer reset the inverter**: TCP/SEMS-side errors (send failure, NACK) no longer discard the UDP inverter connection.
- **Config flow fix**: Removed redundant `read_device_info()` call during setup.

---

# v0.5.0 - Test suite & faster startup

## What's New
- **Non-blocking startup**: Initial inverter connection and SEMS sync now run in a background task, so Home Assistant startup is no longer delayed by UDP probes or the 5-second SEMS TCP ACK timeout.

## Tests
- Added **161 tests** covering all components:
  - `modbus_unpacker`: all data types, unpack/format, edge cases
  - `coordinator`: CRC-16, AES encryption, POSTGW packets, plaintext construction, relay state management, SEMS TCP protocol
  - `config_flow`: user step, confirm step, serial parsing, duplicate detection
  - `__init__`: setup/unload entry, non-blocking startup verification
  - `sensor`: all 4 sensor entities, attributes, restore behaviour
  - `diagnostics`: output structure, redaction, sync status
- Added `pyproject.toml` with test dependencies (`pytest`, `pytest-asyncio`, `pytest-homeassistant-custom-component`)

---

# v0.4.3 - Revert broken refactoring, restore working v0.3.10 code

## What Happened
- v0.4.0 through v0.4.2 broke existing installations by changing the config schema
- Existing config entries stored `inverter_host`/`device_id`/`device_serial` fields
- The refactored code expected `goodwe_entry_id` — a field that didn't exist in stored configs
- This caused "Goodwe integration not found" on every startup

## What This Release Does
- **Reverts all code back to the working v0.3.10 codebase**
- Only change from v0.3.10 is the version number bump
- Existing installations will work again without reconfiguration

---

# v0.3.1 - Direct plaintext construction from goodwe library
