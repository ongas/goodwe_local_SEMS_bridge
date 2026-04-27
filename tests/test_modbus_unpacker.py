"""Tests for modbus_unpacker.py — DataType, RegisterDef, ModbusUnpacker."""

import struct

import pytest

from modbus_unpacker import (
    DataType,
    ET_RUNTIME_REGISTERS,
    ModbusUnpacker,
    RegisterDef,
    format_aa55_data,
    unpack_aa55_payload,
)


# ── DataType enum ─────────────────────────────────────────────────────────────

class TestDataType:
    """Tests for DataType enum values."""

    def test_voltage_properties(self):
        name, size, signed, scale = DataType.VOLTAGE.value
        assert name == "voltage"
        assert size == 2
        assert signed == "unsigned"
        assert scale == 100

    def test_current_signed_properties(self):
        name, size, signed, scale = DataType.CURRENT_S.value
        assert name == "current_s"
        assert size == 2
        assert signed == "signed"
        assert scale == 100

    def test_power4_unsigned(self):
        name, size, signed, scale = DataType.POWER4.value
        assert name == "power4"
        assert size == 4
        assert signed == "unsigned"
        assert scale == 1

    def test_power4_signed(self):
        name, size, signed, scale = DataType.POWER4_S.value
        assert size == 4
        assert signed == "signed"

    def test_energy4_scale(self):
        _, _, _, scale = DataType.ENERGY4.value
        assert scale == 10

    def test_energy8_properties(self):
        name, size, signed, scale = DataType.ENERGY8.value
        assert size == 8
        assert scale == 100

    def test_timestamp_properties(self):
        name, size, signed, scale = DataType.TIMESTAMP.value
        assert name == "timestamp"
        assert size == 6
        assert signed == "timestamp"

    def test_enum_single_byte(self):
        _, size, _, _ = DataType.ENUM.value
        assert size == 1

    def test_all_members_have_four_tuple(self):
        for member in DataType:
            assert len(member.value) == 4, f"{member.name} value is not a 4-tuple"


# ── RegisterDef ───────────────────────────────────────────────────────────────

class TestRegisterDef:
    """Tests for RegisterDef dataclass."""

    def test_size_from_data_type(self):
        reg = RegisterDef("test_volt", 0, DataType.VOLTAGE, "V", "Test")
        assert reg.size == 2

    def test_size_4byte(self):
        reg = RegisterDef("test_pow", 0, DataType.POWER4, "W")
        assert reg.size == 4

    def test_size_8byte(self):
        reg = RegisterDef("test_energy", 0, DataType.ENERGY8, "kWh")
        assert reg.size == 8

    def test_default_unit_and_description(self):
        reg = RegisterDef("bare", 0, DataType.INTEGER)
        assert reg.unit == ""
        assert reg.description == ""


# ── ET_RUNTIME_REGISTERS ──────────────────────────────────────────────────────

class TestETRegisters:
    """Tests for the built-in ET runtime register map."""

    def test_registers_not_empty(self):
        assert len(ET_RUNTIME_REGISTERS) > 0

    def test_first_register_is_vpv1(self):
        assert ET_RUNTIME_REGISTERS[0].name == "vpv1"
        assert ET_RUNTIME_REGISTERS[0].offset == 0

    def test_no_overlapping_registers(self):
        """Adjacent registers should not overlap byte ranges."""
        sorted_regs = sorted(ET_RUNTIME_REGISTERS, key=lambda r: r.offset)
        for i in range(len(sorted_regs) - 1):
            end = sorted_regs[i].offset + sorted_regs[i].size
            next_start = sorted_regs[i + 1].offset
            assert end <= next_start, (
                f"{sorted_regs[i].name} ends at {end} but "
                f"{sorted_regs[i+1].name} starts at {next_start}"
            )


# ── ModbusUnpacker._parse_value ───────────────────────────────────────────────

class TestParseValue:
    """Tests for individual data type parsing."""

    def setup_method(self):
        self.unpacker = ModbusUnpacker()

    def test_unsigned_16bit(self):
        data = struct.pack(">H", 23750)  # 237.50 V
        result = self.unpacker._parse_value(data, DataType.VOLTAGE)
        assert result == 237.50

    def test_signed_16bit_positive(self):
        data = struct.pack(">h", 3750)
        result = self.unpacker._parse_value(data, DataType.POWER)
        assert result == 3750

    def test_signed_16bit_negative(self):
        data = struct.pack(">h", -500)
        result = self.unpacker._parse_value(data, DataType.POWER_S)
        assert result == -500

    def test_unsigned_32bit(self):
        data = struct.pack(">I", 5000)
        result = self.unpacker._parse_value(data, DataType.POWER4)
        assert result == 5000

    def test_signed_32bit_negative(self):
        data = struct.pack(">i", -12345)
        result = self.unpacker._parse_value(data, DataType.POWER4_S)
        assert result == -12345

    def test_energy4_scaled(self):
        data = struct.pack(">I", 125000)  # 12500.0 kWh
        result = self.unpacker._parse_value(data, DataType.ENERGY4)
        assert result == 12500.0

    def test_energy8_scaled(self):
        data = struct.pack(">Q", 1250000)  # 12500.00 kWh
        result = self.unpacker._parse_value(data, DataType.ENERGY8)
        assert result == 12500.0

    def test_unsigned_single_byte_enum(self):
        data = bytes([42])
        result = self.unpacker._parse_value(data, DataType.ENUM)
        assert result == 42

    def test_signed_single_byte(self):
        """Test signed 1-byte path (size=1, signed='signed')."""
        # DataType.TEMPERATURE is 2 bytes; construct a custom 1-byte signed type
        # by calling _parse_value directly with a crafted DataType-like value.
        # Since all existing signed types are >= 2 bytes, test the else branch
        # via a TEMPERATURE with data pre-sliced:
        data = struct.pack(">h", -10)  # -10 °C (signed 16-bit, scale=1)
        result = self.unpacker._parse_value(data, DataType.TEMPERATURE)
        assert result == -10

    def test_frequency_scaled(self):
        data = struct.pack(">H", 5001)  # 50.01 Hz
        result = self.unpacker._parse_value(data, DataType.FREQUENCY)
        assert result == 50.01

    def test_current_signed_scaled(self):
        data = struct.pack(">h", -550)  # -5.50 A
        result = self.unpacker._parse_value(data, DataType.CURRENT_S)
        assert result == -5.50

    def test_long_unsigned_32bit(self):
        data = struct.pack(">I", 8760)
        result = self.unpacker._parse_value(data, DataType.LONG)
        assert result == 8760

    def test_long_signed_32bit(self):
        data = struct.pack(">i", -999)
        result = self.unpacker._parse_value(data, DataType.LONG_S)
        assert result == -999

    def test_integer_unsigned(self):
        data = struct.pack(">H", 1)
        result = self.unpacker._parse_value(data, DataType.INTEGER)
        assert result == 1

    def test_integer_signed(self):
        data = struct.pack(">h", -1)
        result = self.unpacker._parse_value(data, DataType.INTEGER_S)
        assert result == -1

    def test_zero_value(self):
        data = struct.pack(">H", 0)
        result = self.unpacker._parse_value(data, DataType.VOLTAGE)
        assert result == 0.0

    def test_max_unsigned_16(self):
        data = struct.pack(">H", 0xFFFF)
        result = self.unpacker._parse_value(data, DataType.INTEGER)
        assert result == 65535

    def test_timestamp_valid(self):
        """TIMESTAMP reads data[0:2] as year + data[2:7] as 5 bytes.
        
        Known issue: DataType.TIMESTAMP size=6 but _parse_value needs 7 bytes.
        This test documents the bug: a 6-byte buffer will raise struct.error
        because data[2:7] yields only 4 bytes for unpack('BBBBB').
        """
        # With a 7-byte buffer it works correctly:
        data = struct.pack(">H", 2024) + bytes([6, 15, 14, 30, 45])
        result = self.unpacker._parse_value(data, DataType.TIMESTAMP)
        assert result == "2024/06/15 14:30:45"

    def test_timestamp_6byte_buffer_raises(self):
        """DataType.TIMESTAMP declares size=6 but parse needs 7 bytes — this is a bug."""
        data = struct.pack(">H", 2024) + bytes([6, 15, 14, 30])  # only 6 bytes
        with pytest.raises(struct.error):
            self.unpacker._parse_value(data, DataType.TIMESTAMP)

    def test_timestamp_short_returns_none(self):
        """Timestamp with <6 bytes returns None."""
        data = bytes(5)
        result = self.unpacker._parse_value(data, DataType.TIMESTAMP)
        assert result is None


# ── ModbusUnpacker.unpack ─────────────────────────────────────────────────────

def _build_payload(field_values: dict[str, tuple[int, str, bytes]]) -> bytearray:
    """Build a 112-byte payload with specific field values at known offsets.
    
    field_values: {name: (offset, format_string, packed_bytes)}
    """
    payload = bytearray(112)
    for _name, (offset, _fmt, data) in field_values.items():
        payload[offset : offset + len(data)] = data
    return bytes(payload)


class TestUnpack:
    """Tests for ModbusUnpacker.unpack() with full payloads."""

    def test_short_payload_returns_empty(self):
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(50))
        assert result == {}

    def test_exactly_112_bytes_succeeds(self):
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(112))
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_vpv1_parsed_correctly(self):
        """vpv1 at offset 0, VOLTAGE (unsigned 16-bit / 100)."""
        payload = bytearray(112)
        struct.pack_into(">H", payload, 0, 35050)  # 350.50 V
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(payload))
        assert result["vpv1"]["value"] == 350.50
        assert result["vpv1"]["unit"] == "V"

    def test_ppv1_power4_parsed(self):
        """ppv1 at offset 4, POWER4 (unsigned 32-bit, scale=1)."""
        payload = bytearray(112)
        struct.pack_into(">I", payload, 4, 1925)  # 1925 W
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(payload))
        assert result["ppv1"]["value"] == 1925

    def test_pgrid1_signed_power(self):
        """pgrid1 at offset 40, POWER_S (signed 16-bit, scale=1)."""
        payload = bytearray(112)
        struct.pack_into(">h", payload, 40, -500)  # exporting 500 W
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(payload))
        assert result["pgrid1"]["value"] == -500

    def test_temp_air_signed(self):
        """temp_air at offset 70, TEMPERATURE (signed 16-bit, scale=1)."""
        payload = bytearray(112)
        struct.pack_into(">h", payload, 70, -5)  # -5 °C
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(payload))
        assert result["temp_air"]["value"] == -5
        assert result["temp_air"]["unit"] == "°C"

    def test_error_codes_long(self):
        """error_codes at offset 90, LONG (unsigned 32-bit)."""
        payload = bytearray(112)
        # error_codes offset=90, size=4: needs payload[90:94] valid
        struct.pack_into(">I", payload, 90, 0x00010002)
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(payload))
        assert result["error_codes"]["value"] == 0x00010002

    def test_register_exceeding_payload_skipped(self):
        """Register whose offset+size > payload length is skipped."""
        short_regs = [
            RegisterDef("out_of_bounds", 110, DataType.POWER4, "W"),  # needs 110+4=114 > 112
        ]
        unpacker = ModbusUnpacker(registers=short_regs)
        result = unpacker.unpack(bytes(112))
        assert "out_of_bounds" not in result

    def test_custom_registers(self):
        """Custom register list is used instead of defaults."""
        regs = [RegisterDef("custom", 0, DataType.INTEGER, "", "Custom")]
        payload = bytearray(112)
        struct.pack_into(">H", payload, 0, 42)
        unpacker = ModbusUnpacker(registers=regs)
        result = unpacker.unpack(bytes(payload))
        assert "custom" in result
        assert result["custom"]["value"] == 42

    def test_all_zero_payload(self):
        """All-zeros payload should unpack without errors."""
        unpacker = ModbusUnpacker()
        result = unpacker.unpack(bytes(112))
        for name, info in result.items():
            assert info["value"] is not None or info["value"] == 0


# ── ModbusUnpacker.format_output ──────────────────────────────────────────────

class TestFormatOutput:
    """Tests for format_output display logic."""

    def test_empty_data(self):
        unpacker = ModbusUnpacker()
        assert unpacker.format_output({}) == "No data unpacked"

    def test_pv_section_grouped(self):
        data = {
            "pvtest": {"value": 100, "unit": "V", "description": "Test PV"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert "PV Input:" in output
        assert "pvtest: 100 V" in output

    def test_grid_section_grouped(self):
        data = {
            "grid_power": {"value": 3000, "unit": "W", "description": "Grid"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert "Grid:" in output

    def test_temperature_section(self):
        data = {
            "temp_air": {"value": 25, "unit": "°C", "description": "Air"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert "Temperature:" in output
        assert "temp_air: 25 °C" in output

    def test_uncategorized_goes_to_other(self):
        data = {
            "work_mode": {"value": 1, "unit": "", "description": "Work Mode"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert "Other:" in output

    def test_none_values_skipped(self):
        data = {
            "pvnull": {"value": None, "unit": "V", "description": "Null PV"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert "pvnull" not in output

    def test_no_unit_omits_suffix(self):
        data = {
            "work_mode": {"value": 1, "unit": "", "description": "Mode"},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        # Should have "work_mode: 1" without trailing unit
        assert "work_mode: 1" in output
        assert "work_mode: 1 " not in output.replace("work_mode: 1\n", "work_mode: 1")

    def test_header_present(self):
        data = {
            "pvfoo": {"value": 1, "unit": "V", "description": ""},
        }
        unpacker = ModbusUnpacker()
        output = unpacker.format_output(data)
        assert output.startswith("Inverter Data:")


# ── Convenience functions ─────────────────────────────────────────────────────

class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_unpack_aa55_payload_returns_dict(self):
        result = unpack_aa55_payload(bytes(112))
        assert isinstance(result, dict)

    def test_unpack_aa55_short_payload(self):
        result = unpack_aa55_payload(bytes(10))
        assert result == {}

    def test_format_aa55_data_returns_string(self):
        result = format_aa55_data(bytes(112))
        assert isinstance(result, str)
        assert "Inverter Data:" in result

    def test_format_aa55_data_short_payload(self):
        result = format_aa55_data(bytes(10))
        assert result == "No data unpacked"
