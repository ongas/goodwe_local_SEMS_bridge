"""Config flow for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_GOODWE_ENTRY_ID,
    CONF_SEMS_STATION_ID,
    CONF_SYNC_TO_CLOUD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GoodweLocalSemsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GoodWe Local SEMS Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select Goodwe integration."""
        goodwe_entries = self.hass.config_entries.async_entries("goodwe")

        if not goodwe_entries:
            return self.async_abort(reason="no_goodwe_integration")

        # Create a mapping of entry titles to entry IDs
        goodwe_options = {entry.entry_id: entry.title for entry in goodwe_entries}

        if user_input is not None:
            self.context["goodwe_entry_id"] = user_input[CONF_GOODWE_ENTRY_ID]
            return await self.async_step_sems_credentials()

        schema = vol.Schema(
            {
                vol.Required(CONF_GOODWE_ENTRY_ID): vol.In(goodwe_options),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_sems_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to enter SEMS credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Test SEMS authentication
            if await self._test_sems_auth(user_input):
                self.context["sems_data"] = user_input
                return await self.async_step_sync_settings()
            else:
                errors["base"] = "sems_auth_failed"

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(CONF_SEMS_STATION_ID): cv.string,
            }
        )

        return self.async_show_form(
            step_id="sems_credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_sync_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to configure sync settings."""
        if user_input is not None:
            # Create the entry
            goodwe_entry_id = self.context.get("goodwe_entry_id")
            sems_data = self.context.get("sems_data", {})
            goodwe_entries = self.hass.config_entries.async_entries("goodwe")
            goodwe_entry = next(
                (e for e in goodwe_entries if e.entry_id == goodwe_entry_id),
                None,
            )
            
            title = f"GoodWe SEMS Bridge - {goodwe_entry.title}" if goodwe_entry else "GoodWe SEMS Bridge"
            
            return self.async_create_entry(
                title=title,
                data={
                    **sems_data,
                    **user_input,
                    CONF_GOODWE_ENTRY_ID: goodwe_entry_id,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SYNC_TO_CLOUD, default=True
                ): cv.boolean,
            }
        )

        return self.async_show_form(
            step_id="sync_settings",
            data_schema=schema,
        )

    async def _test_sems_auth(self, user_input: dict[str, Any]) -> bool:
        """Test SEMS authentication."""
        try:
            from custom_components.sems.sems_api import SemsApi

            api = SemsApi(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            return api.test_authentication()
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("SEMS authentication test failed: %s", ex)
            return False

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(import_data)
