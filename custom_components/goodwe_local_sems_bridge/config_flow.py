"""Config flow for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from goodwe import InverterError, connect as goodwe_connect

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
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

_LOGGER = logging.getLogger(__name__)


async def _connect_and_probe(
    hass: HomeAssistant, host: str, port: int
) -> tuple[Any | None, str | None]:
    """Connect to the inverter, read raw running-data response, return (inverter, error_key)."""
    try:
        inverter = await goodwe_connect(host, port)
        await inverter.read_device_info()
        return inverter, None
    except InverterError:
        return None, "cannot_connect"
    except Exception:  # pylint: disable=broad-except
        return None, "cannot_connect"



class GoodweLocalSemsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GoodWe Local SEMS Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._inverter_host: str = ""
        self._inverter_port: int = DEFAULT_INVERTER_PORT
        self._inverter: Any = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Enter the inverter IP address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_INVERTER_HOST].strip()
            port = user_input.get(CONF_INVERTER_PORT, DEFAULT_INVERTER_PORT)

            inverter, error_key = await _connect_and_probe(self.hass, host, port)
            if error_key:
                errors["base"] = error_key
            else:
                # Prevent duplicate entries for the same inverter serial
                await self.async_set_unique_id(inverter.serial_number)
                self._abort_if_unique_id_configured()

                self._inverter = inverter
                self._inverter_host = host
                self._inverter_port = port
                return await self.async_step_confirm()

        schema = vol.Schema(
            {
                vol.Required(CONF_INVERTER_HOST): cv.string,
                vol.Optional(CONF_INVERTER_PORT, default=DEFAULT_INVERTER_PORT): cv.port,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Confirm detected inverter and learn device header."""
        if user_input is not None:
            inv = self._inverter

            # Device header is the 21-byte firmware-level POSTGW prefix — constant per model.
            # It is NOT readable via the modbus/goodwe library (it's prepended by the inverter
            # firmware itself). Use the known constant derived from MITM captures.
            device_header = KNOWN_DT_DEVICE_HEADER_HEX

            # Device ID and serial come from the inverter's serial number field
            # GoodWe serial numbers are 16 chars: first 8 = device_id, last 8 = device_serial
            sn = inv.serial_number or ""
            device_id = sn[:8].rstrip("\x00") if len(sn) >= 8 else sn
            device_serial = sn[8:16].rstrip("\x00") if len(sn) >= 16 else ""

            _LOGGER.info(
                "Inverter confirmed: model=%s sn=%s device_id=%s device_serial=%s",
                inv.model_name, sn, device_id, device_serial,
            )

            return self.async_create_entry(
                title=f"GoodWe SEMS Bridge ({inv.model_name or sn})",
                data={
                    CONF_INVERTER_HOST: self._inverter_host,
                    CONF_INVERTER_PORT: self._inverter_port,
                    CONF_MODEL_FAMILY: type(inv).__name__,
                    CONF_DEVICE_HEADER: device_header,
                    CONF_DEVICE_ID: device_id,
                    CONF_DEVICE_SERIAL: device_serial,
                },
            )

        inv = self._inverter
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "model": inv.model_name or "Unknown",
                "serial": inv.serial_number or "Unknown",
                "host": self._inverter_host,
            },
        )

