"""Tests for config_flow.py — GoodWe Local SEMS Bridge config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from goodwe import InverterError

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.goodwe_local_sems_bridge.const import (
    CONF_DEVICE_HEADER,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_INVERTER_HOST,
    CONF_INVERTER_PORT,
    CONF_MODEL_FAMILY,
    DEFAULT_INVERTER_PORT,
    DOMAIN,
    KNOWN_DT_DEVICE_HEADER_HEX,
)

from tests.conftest import FakeInverter


@pytest.fixture
def fake_inverter():
    return FakeInverter()


# ── Step: user ────────────────────────────────────────────────────────────────

class TestUserStep:
    """Tests for the initial user input step."""

    async def test_form_shown_on_first_load(self, hass: HomeAssistant) -> None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    async def test_connection_error(self, hass: HomeAssistant) -> None:
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            side_effect=InverterError("timeout"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.100"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_generic_exception(self, hass: HomeAssistant) -> None:
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            side_effect=Exception("unexpected"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.100"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_successful_connect_advances_to_confirm(
        self, hass: HomeAssistant, fake_inverter
    ) -> None:
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=fake_inverter,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.100"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"

    async def test_custom_port(self, hass: HomeAssistant, fake_inverter) -> None:
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=fake_inverter,
        ) as mock_connect:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={
                    CONF_INVERTER_HOST: "192.168.1.200",
                    CONF_INVERTER_PORT: 9000,
                },
            )

        assert result["step_id"] == "confirm"
        mock_connect.assert_called_once_with("192.168.1.200", 9000)

    async def test_host_whitespace_stripped(
        self, hass: HomeAssistant, fake_inverter
    ) -> None:
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=fake_inverter,
        ) as mock_connect:
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "  192.168.1.100  "},
            )

        mock_connect.assert_called_once_with("192.168.1.100", DEFAULT_INVERTER_PORT)


# ── Step: confirm ─────────────────────────────────────────────────────────────

class TestConfirmStep:
    """Tests for the confirmation step that creates the config entry."""

    async def _get_to_confirm(
        self,
        hass: HomeAssistant,
        inverter: FakeInverter | None = None,
    ) -> str:
        """Run through user step and return the flow_id at confirm step."""
        inv = inverter or FakeInverter()
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=inv,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.100"},
            )
        assert result["step_id"] == "confirm"
        return result["flow_id"]

    async def test_confirm_creates_entry(self, hass: HomeAssistant) -> None:
        flow_id = await self._get_to_confirm(hass)

        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert "GoodWe SEMS Bridge" in result["title"]
        data = result["data"]
        assert data[CONF_INVERTER_HOST] == "192.168.1.100"
        assert data[CONF_INVERTER_PORT] == DEFAULT_INVERTER_PORT
        assert data[CONF_DEVICE_HEADER] == KNOWN_DT_DEVICE_HEADER_HEX
        assert data[CONF_DEVICE_ID] == "12345678"
        assert data[CONF_DEVICE_SERIAL] == "ABCDEFGH"

    async def test_confirm_model_family(self, hass: HomeAssistant) -> None:
        flow_id = await self._get_to_confirm(hass)
        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
        # type(FakeInverter).__name__ = "FakeInverter"
        assert result["data"][CONF_MODEL_FAMILY] == "FakeInverter"

    async def test_confirm_short_serial(self, hass: HomeAssistant) -> None:
        """Short serial → device_id is the full SN, device_serial is empty."""
        inv = FakeInverter(serial_number="SHORT")
        flow_id = await self._get_to_confirm(hass, inverter=inv)
        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
        assert result["data"][CONF_DEVICE_ID] == "SHORT"
        assert result["data"][CONF_DEVICE_SERIAL] == ""

    async def test_confirm_null_padded_serial(self, hass: HomeAssistant) -> None:
        """Null-padded serial should be stripped."""
        inv = FakeInverter(serial_number="1234\x00\x00\x00\x00ABCD\x00\x00\x00\x00")
        flow_id = await self._get_to_confirm(hass, inverter=inv)
        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
        assert result["data"][CONF_DEVICE_ID] == "1234"
        assert result["data"][CONF_DEVICE_SERIAL] == "ABCD"

    async def test_confirm_exactly_8_char_serial(self, hass: HomeAssistant) -> None:
        """8-char serial → device_id is full SN, device_serial is empty."""
        inv = FakeInverter(serial_number="12345678")
        flow_id = await self._get_to_confirm(hass, inverter=inv)
        result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
        assert result["data"][CONF_DEVICE_ID] == "12345678"
        assert result["data"][CONF_DEVICE_SERIAL] == ""

    async def test_duplicate_serial_aborted(self, hass: HomeAssistant) -> None:
        """Second config entry with same serial should be aborted."""
        inv = FakeInverter(serial_number="12345678ABCDEFGH")

        # First entry succeeds
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=inv,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.100"},
            )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

        # Second entry with same serial should abort
        with patch(
            "custom_components.goodwe_local_sems_bridge.config_flow.goodwe_connect",
            return_value=inv,
        ):
            result2 = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_INVERTER_HOST: "192.168.1.200"},
            )

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"

    async def test_confirm_shows_description(self, hass: HomeAssistant) -> None:
        """Confirm step should include model/serial/host placeholders."""
        inv = FakeInverter()
        flow_id = await self._get_to_confirm(hass, inverter=inv)

        # Re-show the form without user_input
        result = await hass.config_entries.flow.async_configure(flow_id)
        assert result["type"] == FlowResultType.FORM
        placeholders = result["description_placeholders"]
        assert placeholders["model"] == "GW10K-ET"
        assert placeholders["serial"] == "12345678ABCDEFGH"
        assert placeholders["host"] == "192.168.1.100"
