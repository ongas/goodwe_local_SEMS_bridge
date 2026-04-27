"""Shared test fixtures for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant import loader

from custom_components.goodwe_local_sems_bridge.const import (
    CONF_DEVICE_HEADER,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_INVERTER_HOST,
    CONF_INVERTER_PORT,
    CONF_MODEL_FAMILY,
    DOMAIN,
    KNOWN_DT_DEVICE_HEADER_HEX,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations and clean up editable-install path hooks."""
    import custom_components

    original_path = list(custom_components.__path__)
    # Filter out editable-install path hooks that aren't real directories
    custom_components.__path__ = [
        p for p in custom_components.__path__
        if not p.startswith("__editable__")
    ]
    yield
    custom_components.__path__ = original_path

MOCK_CONFIG_DATA: dict[str, Any] = {
    CONF_INVERTER_HOST: "192.168.1.100",
    CONF_INVERTER_PORT: 8899,
    CONF_MODEL_FAMILY: "ET",
    CONF_DEVICE_HEADER: KNOWN_DT_DEVICE_HEADER_HEX,
    CONF_DEVICE_ID: "12345678",
    CONF_DEVICE_SERIAL: "ABCDEFGH",
}


class FakeInverter:
    """Fake inverter object returned by goodwe.connect for testing."""

    def __init__(
        self,
        serial_number: str = "12345678ABCDEFGH",
        model_name: str = "GW10K-ET",
    ) -> None:
        self.serial_number = serial_number
        self.model_name = model_name

    async def read_device_info(self) -> None:
        pass

    async def read_runtime_data(self) -> dict[str, Any]:
        return _sample_runtime_data()


def _sample_runtime_data() -> dict[str, Any]:
    """Return a realistic set of runtime data values."""
    return {
        "vpv1": 350.0,
        "ipv1": 5.5,
        "vpv2": 340.0,
        "ipv2": 5.3,
        "vpv3": 0.0,
        "ipv3": 0.0,
        "vline1": 410.0,
        "vline2": 408.0,
        "vline3": 412.0,
        "vgrid1": 237.5,
        "vgrid2": 236.8,
        "vgrid3": 238.2,
        "igrid1": 8.5,
        "igrid2": 8.4,
        "igrid3": 8.6,
        "fgrid1": 50.01,
        "fgrid2": 50.02,
        "fgrid3": 50.00,
        "ppv": 3750,
        "work_mode": 1,
        "error_codes": 0,
        "warning_code": 0,
        "apparent_power": 3800,
        "reactive_power": -50,
        "power_factor": 0.987,
        "temperature": 42.5,
        "e_day": 18.5,
        "e_total": 12500.0,
        "h_total": 8760,
        "safety_country": 32,
        "funbit": 0,
        "vbus": 380.0,
        "vnbus": 190.0,
        "derating_mode": 0,
    }


@pytest.fixture
def sample_runtime_data() -> dict[str, Any]:
    """Fixture providing sample runtime data."""
    return _sample_runtime_data()


@pytest.fixture
def mock_config_entry(hass: HomeAssistant):
    """Create a mock config entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="GoodWe SEMS Bridge (GW10K-ET)",
        data=MOCK_CONFIG_DATA.copy(),
        entry_id="test_entry_id",
        unique_id="12345678ABCDEFGH",
    )
    entry.add_to_hass(hass)
    return entry
