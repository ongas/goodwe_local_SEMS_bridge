"""Tests for diagnostics.py — diagnostics export and redaction."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.goodwe_local_sems_bridge.const import (
    CONF_DEVICE_HEADER,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_INVERTER_HOST,
    DOMAIN,
)
from custom_components.goodwe_local_sems_bridge.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.goodwe_local_sems_bridge.coordinator import GoodweLocalSemsRelay

from tests.conftest import MOCK_CONFIG_DATA


def _make_relay() -> GoodweLocalSemsRelay:
    hass = MagicMock()
    return GoodweLocalSemsRelay(
        hass=hass,
        inverter_host="192.168.1.100",
        inverter_port=8899,
        model_family="ET",
        device_header_hex="067552755704570000000e000001310001759475c5",
        device_id="12345678",
        device_serial="ABCDEFGH",
    )


class TestDiagnostics:
    """Tests for diagnostics export."""

    async def test_diagnostics_structure(self, hass: HomeAssistant, mock_config_entry) -> None:
        """Diagnostics should return expected top-level keys."""
        relay = _make_relay()
        relay._last_sems_sync = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        relay._sync_count = 5
        relay._sems_sync_failed = False
        relay._last_error = None
        relay.last_runtime_data = {"vpv1": 350.0, "ppv": 3750}

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = relay

        result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        assert "config" in result
        assert "sync_status" in result
        assert "runtime_data_keys" in result

    async def test_diagnostics_redacts_sensitive_data(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Sensitive fields should be redacted."""
        relay = _make_relay()
        relay._last_sems_sync = None
        relay._sync_count = 0
        relay._sems_sync_failed = False
        relay._last_error = None
        relay.last_runtime_data = {}

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = relay

        result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        config = result["config"]
        assert config[CONF_INVERTER_HOST] == "**REDACTED**"
        assert config[CONF_DEVICE_ID] == "**REDACTED**"
        assert config[CONF_DEVICE_SERIAL] == "**REDACTED**"

    async def test_diagnostics_device_header_not_redacted(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Device header is not in the redaction list."""
        relay = _make_relay()
        relay._last_sems_sync = None
        relay._sync_count = 0
        relay._sems_sync_failed = False
        relay._last_error = None
        relay.last_runtime_data = {}

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = relay

        result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        assert result["config"][CONF_DEVICE_HEADER] != "**REDACTED**"

    async def test_diagnostics_sync_status(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Sync status fields should be populated correctly."""
        ts = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        relay = _make_relay()
        relay._last_sems_sync = ts
        relay._sync_count = 42
        relay._sems_sync_failed = True
        relay._last_error = "NACK"
        relay.last_runtime_data = {}

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = relay

        result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        sync = result["sync_status"]
        assert sync["last_sync"] == ts.isoformat()
        assert sync["sync_count"] == 42
        assert sync["last_sync_failed"] is True
        assert sync["last_error"] == "NACK"

    async def test_diagnostics_runtime_data_keys(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Runtime data keys should be listed."""
        relay = _make_relay()
        relay._last_sems_sync = None
        relay._sync_count = 0
        relay._sems_sync_failed = False
        relay._last_error = None
        relay.last_runtime_data = {"vpv1": 350.0, "ppv": 3750, "temperature": 42.5}

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_config_entry.entry_id] = relay

        result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        keys = result["runtime_data_keys"]
        assert "vpv1" in keys
        assert "ppv" in keys
        assert "temperature" in keys
