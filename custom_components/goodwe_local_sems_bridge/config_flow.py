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
)

_LOGGER = logging.getLogger(__name__)

# Fixed-size constants for the POSTGW plaintext format
_DEVICE_HEADER_SIZE = 21   # bytes 0x00–0x14 of the 240-byte POSTGW plaintext
_MODBUS_DATA_SIZE = 219    # bytes 0x15–0xEF of the 240-byte POSTGW plaintext


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


async def _read_device_header(hass: HomeAssistant, inverter: Any) -> str | None:
    """Read one raw running-data response, extract the 21-byte device header, return as hex.

    The 240-byte POSTGW plaintext = device_header(21) + modbus_response_data[:219].
    The device header is constant per device (firmware/model bytes prepended by inverter firmware).
    We obtain it by reading a real captured plaintext from the inverter via modbus, then capturing
    the first 21 bytes from an actual POSTGW packet.

    Since we cannot intercept POSTGW directly here, we use the known-stable approach:
    attempt to get the header from the goodwe library's raw response.
    The first 21 bytes of the POSTGW plaintext correspond to the first 21 bytes of the
    trimmed running-data response returned by the inverter.
    """
    try:
        response = await inverter._read_from_socket(  # pylint: disable=protected-access
            inverter._READ_RUNNING_DATA  # pylint: disable=protected-access
        )
        raw = response.response_data()
        if len(raw) < _DEVICE_HEADER_SIZE + _MODBUS_DATA_SIZE:
            _LOGGER.warning(
                "Running data response too short: %d bytes (need %d)",
                len(raw), _DEVICE_HEADER_SIZE + _MODBUS_DATA_SIZE,
            )
            return None
        # The first 21 bytes of the trimmed response ARE the device header
        header_hex = raw[:_DEVICE_HEADER_SIZE].hex()
        _LOGGER.debug("Device header learned: %s", header_hex)
        return header_hex
    except Exception as ex:  # pylint: disable=broad-except
        _LOGGER.warning("Failed to read device header: %s", ex)
        return None


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

            # Read the 21-byte device header from the inverter's raw running-data response
            device_header = await _read_device_header(self.hass, inv)
            if not device_header:
                return self.async_abort(reason="cannot_read_header")

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

