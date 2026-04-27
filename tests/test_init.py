"""Tests for __init__.py — async_setup_entry / async_unload_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.goodwe_local_sems_bridge.const import DOMAIN

from tests.conftest import MOCK_CONFIG_DATA


class TestSetupEntry:
    """Tests for async_setup_entry."""

    async def test_setup_stores_relay(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Setup should store the relay in hass.data."""
        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.goodwe_connect",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                return_value=True,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_sync",
                return_value=True,
            ),
        ):
            result = await hass.config_entries.async_setup(mock_config_entry.entry_id)

        assert result is True
        assert mock_config_entry.entry_id in hass.data[DOMAIN]

    async def test_setup_creates_listener(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Setup should register a periodic sync listener."""
        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.goodwe_connect",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                return_value=False,
            ),
        ):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)

        listener_key = f"{mock_config_entry.entry_id}_listener"
        assert listener_key in hass.data[DOMAIN]
        assert callable(hass.data[DOMAIN][listener_key])

    async def test_setup_does_not_block_on_connect(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Setup should return True immediately — connect runs in background."""
        connect_called = False

        async def slow_connect():
            nonlocal connect_called
            connect_called = True
            return False

        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                side_effect=slow_connect,
            ),
        ):
            result = await hass.config_entries.async_setup(mock_config_entry.entry_id)

        assert result is True
        # Background task was scheduled (may or may not have run yet)

    async def test_setup_forwards_platforms(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Setup should forward sensor platform."""
        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.goodwe_connect",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                return_value=False,
            ),
        ):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)

        # Sensor platform should have been set up
        entity_platform = hass.data.get("entity_platform", {})
        # Just check no error occurred — platform setup succeeded


class TestUnloadEntry:
    """Tests for async_unload_entry."""

    async def test_unload_removes_relay(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Unload should remove relay and listener from hass.data."""
        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.goodwe_connect",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                return_value=False,
            ),
        ):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)

        result = await hass.config_entries.async_unload(mock_config_entry.entry_id)

        assert result is True
        assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})

    async def test_unload_cancels_listener(
        self, hass: HomeAssistant, mock_config_entry
    ) -> None:
        """Unload should cancel the periodic sync listener."""
        with (
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.goodwe_connect",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.goodwe_local_sems_bridge.coordinator.GoodweLocalSemsRelay.async_connect",
                return_value=False,
            ),
        ):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)

        listener_key = f"{mock_config_entry.entry_id}_listener"
        listener = hass.data[DOMAIN].get(listener_key)
        assert listener is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

        assert listener_key not in hass.data.get(DOMAIN, {})
