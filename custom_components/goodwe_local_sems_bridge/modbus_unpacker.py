"""
Modbus register unpacker for AA55 payload analysis.

This module provides utilities to unpack and interpret Modbus register data
extracted from AA55 encrypted packets. Used for non-invasive inspection of
inverter telemetry data during interception phase.
"""

import struct
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple
from enum import Enum

_LOGGER = logging.getLogger(__name__)


class DataType(Enum):
    """Modbus data type definitions with size and interpretation rules."""
    
    # Single register types (2 bytes)
    VOLTAGE = ("voltage", 2, "unsigned", 100)  # Value / 100
    CURRENT = ("current", 2, "unsigned", 100)  # Value / 100
    CURRENT_S = ("current_s", 2, "signed", 100)  # Signed, / 100
    POWER = ("power", 2, "signed", 1)  # Signed, raw value
    POWER_S = ("power_s", 2, "signed", 1)  # Signed, raw value
    FREQUENCY = ("frequency", 2, "unsigned", 100)  # Value / 100
    TEMPERATURE = ("temperature", 2, "signed", 1)  # Signed, raw °C
    INTEGER = ("integer", 2, "unsigned", 1)  # Unsigned integer
    INTEGER_S = ("integer_s", 2, "signed", 1)  # Signed integer
    ENUM = ("enum", 1, "unsigned", 1)  # Single byte enum
    
    # Double register types (4 bytes)
    POWER4 = ("power4", 4, "unsigned", 1)  # 32-bit unsigned power
    POWER4_S = ("power4_s", 4, "signed", 1)  # 32-bit signed power
    ENERGY4 = ("energy4", 4, "unsigned", 10)  # Value / 10 for kWh
    LONG = ("long", 4, "unsigned", 1)  # 32-bit unsigned integer
    LONG_S = ("long_s", 4, "signed", 1)  # 32-bit signed integer
    
    # Triple register types (6 bytes)
    TIMESTAMP = ("timestamp", 6, "timestamp", 1)  # YYYY/MM/DD HH:MM:SS
    
    # Quad register types (8 bytes)
    ENERGY8 = ("energy8", 8, "unsigned", 100)  # Value / 100 for kWh


@dataclass
class RegisterDef:
    """Definition of a single Modbus register field."""
    
    name: str
    offset: int  # Offset within payload (in bytes)
    data_type: DataType
    unit: str = ""
    description: str = ""
    
    @property
    def size(self) -> int:
        """Return size in bytes."""
        return self.data_type.value[1]


# ET Platform Main Runtime Register Map (most common)
ET_RUNTIME_REGISTERS = [
    # PV Input Section (35103-35120)
    RegisterDef("vpv1", 0, DataType.VOLTAGE, "V", "PV String 1 Voltage"),
    RegisterDef("ipv1", 2, DataType.CURRENT, "A", "PV String 1 Current"),
    RegisterDef("ppv1", 4, DataType.POWER4, "W", "PV String 1 Power"),
    
    RegisterDef("vpv2", 8, DataType.VOLTAGE, "V", "PV String 2 Voltage"),
    RegisterDef("ipv2", 10, DataType.CURRENT, "A", "PV String 2 Current"),
    RegisterDef("ppv2", 12, DataType.POWER4, "W", "PV String 2 Power"),
    
    RegisterDef("vpv3", 16, DataType.VOLTAGE, "V", "PV String 3 Voltage"),
    RegisterDef("ipv3", 18, DataType.CURRENT, "A", "PV String 3 Current"),
    RegisterDef("ppv3", 20, DataType.POWER4, "W", "PV String 3 Power"),
    
    RegisterDef("vpv4", 24, DataType.VOLTAGE, "V", "PV String 4 Voltage"),
    RegisterDef("ipv4", 26, DataType.CURRENT, "A", "PV String 4 Current"),
    RegisterDef("ppv4", 28, DataType.POWER4, "W", "PV String 4 Power"),
    
    # AC Grid Section (35121-35140)
    RegisterDef("vgrid1", 32, DataType.VOLTAGE, "V", "Grid Phase 1 Voltage"),
    RegisterDef("igrid1", 34, DataType.CURRENT, "A", "Grid Phase 1 Current"),
    RegisterDef("fgrid1", 36, DataType.FREQUENCY, "Hz", "Grid Phase 1 Frequency"),
    # pad 38
    RegisterDef("pgrid1", 40, DataType.POWER_S, "W", "Grid Phase 1 Power"),
    
    RegisterDef("vgrid2", 42, DataType.VOLTAGE, "V", "Grid Phase 2 Voltage"),
    RegisterDef("igrid2", 44, DataType.CURRENT, "A", "Grid Phase 2 Current"),
    RegisterDef("fgrid2", 46, DataType.FREQUENCY, "Hz", "Grid Phase 2 Frequency"),
    # pad 48
    RegisterDef("pgrid2", 50, DataType.POWER_S, "W", "Grid Phase 2 Power"),
    
    RegisterDef("vgrid3", 52, DataType.VOLTAGE, "V", "Grid Phase 3 Voltage"),
    RegisterDef("igrid3", 54, DataType.CURRENT, "A", "Grid Phase 3 Current"),
    RegisterDef("fgrid3", 56, DataType.FREQUENCY, "Hz", "Grid Phase 3 Frequency"),
    # pad 58
    RegisterDef("pgrid3", 60, DataType.POWER_S, "W", "Grid Phase 3 Power"),
    
    RegisterDef("grid_mode", 62, DataType.INTEGER, "", "Grid Mode"),
    RegisterDef("total_inverter_power", 64, DataType.POWER_S, "W", "Total Inverter Power"),
    # pad 66
    RegisterDef("active_power", 68, DataType.POWER_S, "W", "Active Power"),
    
    # Temperature Section (35174-35179)
    RegisterDef("temp_air", 70, DataType.TEMPERATURE, "°C", "Air Temperature"),
    RegisterDef("temp_module", 72, DataType.TEMPERATURE, "°C", "Module Temperature"),
    RegisterDef("temperature", 74, DataType.TEMPERATURE, "°C", "Primary Temperature"),
    # pad 76
    RegisterDef("bus_voltage", 78, DataType.VOLTAGE, "V", "Bus Voltage"),
    RegisterDef("nbus_voltage", 80, DataType.VOLTAGE, "V", "N-Bus Voltage"),
    
    # Status & Codes (35185-35189)
    RegisterDef("warning_code", 82, DataType.INTEGER, "", "Warning Code"),
    RegisterDef("safety_country", 84, DataType.INTEGER, "", "Safety Country"),
    RegisterDef("work_mode", 86, DataType.INTEGER, "", "Work Mode"),
    RegisterDef("operation_mode", 88, DataType.INTEGER, "", "Operation Mode"),
    RegisterDef("error_codes", 90, DataType.LONG, "", "Error Codes"),
]


class ModbusUnpacker:
    """Unpacker for Modbus register payload data."""
    
    def __init__(self, registers: Optional[list] = None):
        """
        Initialize unpacker with register definitions.
        
        Args:
            registers: List of RegisterDef objects. Defaults to ET_RUNTIME_REGISTERS.
        """
        self.registers = registers or ET_RUNTIME_REGISTERS
    
    def unpack(self, payload: bytes) -> Dict[str, Any]:
        """
        Unpack all registers from payload.
        
        Args:
            payload: Raw 112-byte decrypted AA55 payload
            
        Returns:
            Dictionary of {field_name: value} with units applied
        """
        if len(payload) < 112:
            _LOGGER.warning(
                "Payload too short: %d bytes (expected 112)",
                len(payload)
            )
            return {}
        
        result = {}
        
        for reg in self.registers:
            try:
                offset = reg.offset
                if offset + reg.size > len(payload):
                    _LOGGER.debug(
                        "Register %s offset %d+%d exceeds payload size",
                        reg.name, offset, reg.size
                    )
                    continue
                
                value = self._parse_value(
                    payload[offset : offset + reg.size],
                    reg.data_type
                )
                
                # Store both raw and formatted
                result[reg.name] = {
                    "value": value,
                    "unit": reg.unit,
                    "description": reg.description,
                }
                
            except Exception as e:
                _LOGGER.debug(
                    "Error unpacking register %s: %s",
                    reg.name, str(e)
                )
        
        return result
    
    def _parse_value(self, data: bytes, dtype: DataType) -> Any:
        """Parse raw bytes according to data type."""
        type_name, size, signed, scale = dtype.value
        
        if type_name == "timestamp":
            # 6 bytes: YYYY MM DD HH MM SS
            if len(data) < 6:
                return None
            year = struct.unpack(">H", data[0:2])[0]
            month, day, hour, minute, second = struct.unpack("BBBBB", data[2:7])
            return f"{year:04d}/{month:02d}/{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        
        elif signed == "signed":
            if size == 2:
                value = struct.unpack(">h", data[0:2])[0]
            elif size == 4:
                value = struct.unpack(">i", data[0:4])[0]
            elif size == 8:
                value = struct.unpack(">q", data[0:8])[0]
            else:
                value = struct.unpack("b", data[0:1])[0]
        
        elif signed == "unsigned":
            if size == 2:
                value = struct.unpack(">H", data[0:2])[0]
            elif size == 4:
                value = struct.unpack(">I", data[0:4])[0]
            elif size == 8:
                value = struct.unpack(">Q", data[0:8])[0]
            else:
                value = struct.unpack("B", data[0:1])[0]
        
        else:
            value = 0
        
        # Apply scale factor
        if scale != 1:
            value = round(value / scale, 2)
        
        return value
    
    def format_output(self, data: Dict[str, Any]) -> str:
        """
        Format unpacked data as readable string.
        
        Args:
            data: Dictionary from unpack()
            
        Returns:
            Formatted string for logging
        """
        if not data:
            return "No data unpacked"
        
        lines = ["Inverter Data:"]
        
        # Group by category
        categories = {
            "pv": "PV Input",
            "grid": "Grid",
            "temp": "Temperature",
            "battery": "Battery",
            "load": "Load",
            "meter": "Meter",
        }
        
        for prefix, label in categories.items():
            section_data = {
                k: v for k, v in data.items()
                if k.lower().startswith(prefix)
            }
            
            if section_data:
                lines.append(f"\n{label}:")
                for name in sorted(section_data.keys()):
                    info = section_data[name]
                    if info["value"] is not None:
                        line = f"  {name}: {info['value']}"
                        if info["unit"]:
                            line += f" {info['unit']}"
                        lines.append(line)
        
        # Any remaining fields
        remaining = {
            k: v for k, v in data.items()
            if not any(k.lower().startswith(p) for p in categories.keys())
        }
        
        if remaining:
            lines.append("\nOther:")
            for name in sorted(remaining.keys()):
                info = remaining[name]
                if info["value"] is not None:
                    line = f"  {name}: {info['value']}"
                    if info["unit"]:
                        line += f" {info['unit']}"
                    lines.append(line)
        
        return "\n".join(lines)


def unpack_aa55_payload(payload: bytes) -> Dict[str, Any]:
    """
    Convenience function to unpack an AA55 payload.
    
    Args:
        payload: Raw 112-byte decrypted AA55 payload
        
    Returns:
        Dictionary of unpacked register values
    """
    unpacker = ModbusUnpacker()
    return unpacker.unpack(payload)


def format_aa55_data(payload: bytes) -> str:
    """
    Convenience function to unpack and format AA55 data for logging.
    
    Args:
        payload: Raw 112-byte decrypted AA55 payload
        
    Returns:
        Formatted string ready for logging
    """
    unpacker = ModbusUnpacker()
    data = unpacker.unpack(payload)
    return unpacker.format_output(data)
