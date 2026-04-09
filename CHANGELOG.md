# v2.3.1 - Critical Diagnostics Import Fix

## Bug Fixes
- **CRITICAL**: Fixed blocking import error in diagnostics module that prevented Home Assistant from loading the integration
- Updated diagnostics to use correct configuration constants from refactored config flow
- Removed references to deprecated device configuration fields

---

# v2.3.0 - Integration Refactor & Configuration Simplification

## What's New

Complete refactor of the integration architecture to simplify configuration and improve reliability:
- **Simplified Configuration**: Now integrates with the standard GoodWe integration instead of requiring separate Modbus configuration
- **Code Cleanup**: Significant refactoring of coordinator, config flow, and sensor platform for improved maintainability
- **Documentation Improvements**: Streamlined README with clearer feature descriptions and problem statement
- **Dependency Tracking**: Integration now validates Goodwe integration dependency and handles missing dependencies gracefully

## Key Improvements

### Configuration Flow
- Changed from direct inverter host/port configuration to selecting existing Goodwe integration
- Added SEMS cloud sync toggle (enable/disable cloud synchronization)
- Improved configuration validation and error handling

### Code Quality
- Refactored coordinator with cleaner async patterns
- Simplified sensor definitions and platform initialization
- Improved error messages and logging
- Updated diagnostics module for better troubleshooting

### Documentation
- Simplified README focusing on problem-solution narrative
- Clearer feature list with emoji indicators
- Improved installation instructions
- Removed technical protocol details from main README

## Bug Fixes
- Fixed configuration validation when Goodwe integration is not available
- Improved handling of SEMS sync failures without blocking local operation

### Full Sensor List

| Sensor | Unit | Type | Description |
|--------|------|------|-------------|
| goodwe_local_sems_bridge_input_voltage | V | measurement | AC input voltage |
| goodwe_local_sems_bridge_input_frequency | Hz | measurement | AC input frequency |
| goodwe_local_sems_bridge_pv_voltage | V | measurement | PV DC voltage |
| goodwe_local_sems_bridge_pv_current | A | measurement | PV DC current |
| goodwe_local_sems_bridge_battery_current | A | measurement | Battery charge current |
| goodwe_local_sems_bridge_charging_total | Wh | total_increasing | Total energy charged |
| goodwe_local_sems_bridge_discharging_total | Wh | total_increasing | Total energy discharged |
| goodwe_local_sems_bridge_power_factor | % | measurement | Power factor |
| goodwe_local_sems_bridge_efficiency | % | measurement | Inverter efficiency |

---

# v2.2.1 - Direct plaintext construction from goodwe library
