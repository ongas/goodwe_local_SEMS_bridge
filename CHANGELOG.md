# v2.3.3 - Revert broken refactoring, restore working v2.2.10 code

## What Happened
- v2.3.0 through v2.3.2 broke existing installations by changing the config schema
- Existing config entries stored `inverter_host`/`device_id`/`device_serial` fields
- The refactored code expected `goodwe_entry_id` — a field that didn't exist in stored configs
- This caused "Goodwe integration not found" on every startup

## What This Release Does
- **Reverts all code back to the working v2.2.10 codebase**
- Only change from v2.2.10 is the version number bump
- Existing installations will work again without reconfiguration

---

# v2.2.1 - Direct plaintext construction from goodwe library
