"""Config flow for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_AA55_PROXY_ENABLED,
    CONF_AA55_PROXY_PORT,
    CONF_DEVICE_ID,
    CONF_DEVICE_SERIAL,
    CONF_GOODWE_ENTRY_ID,
    CONF_SEMS_STATION_ID,
    CONF_SYNC_TO_CLOUD,
    DEFAULT_AA55_PROXY_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SEMS_LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
SEMS_STATION_LIST_URL = "https://www.semsportal.com/api/v3/PowerStation/GetStationList"
SEMS_MONITOR_URL = "https://www.semsportal.com/api/v3/PowerStation/GetMonitorDetailByPowerstationId"
SEMS_BASE_TOKEN = json.dumps({"version": "", "client": "ios", "language": "en"})


async def _sems_fetch_stations(hass: HomeAssistant, username: str, password: str) -> tuple[dict | None, list | None, str | None]:
    """Authenticate to SEMS and return (token_data, stations, error_key)."""
    session = async_get_clientsession(hass)

    # Step 1: Authenticate
    try:
        resp = await session.post(
            SEMS_LOGIN_URL,
            json={"account": username, "pwd": password},
            headers={"Content-Type": "application/json", "token": SEMS_BASE_TOKEN},
            timeout=15,
        )
        data = await resp.json(content_type=None)
    except Exception:
        return None, None, "cannot_connect"

    if data.get("code") not in (0, "0"):
        return None, None, "invalid_auth"

    token_data = data.get("data", {})
    if not isinstance(token_data, dict):
        return None, None, "invalid_auth"

    # Step 2: Fetch station list
    try:
        resp = await session.post(
            SEMS_STATION_LIST_URL,
            json={"page": 1, "per_page": 50},
            headers={
                "Content-Type": "application/json",
                "token": json.dumps(token_data),
            },
            timeout=15,
        )
        ps_data = await resp.json(content_type=None)
    except Exception:
        return token_data, None, "cannot_fetch_stations"

    if ps_data.get("code") not in (0, "0"):
        return token_data, None, "cannot_fetch_stations"

    stations = ps_data.get("data", [])
    if isinstance(stations, dict):
        stations = [stations]

    return token_data, stations, None


async def _sems_fetch_inverters(hass: HomeAssistant, token_data: dict, station_id: str) -> tuple[list | None, str | None]:
    """Fetch inverters for a station. Returns (inverters, error_key).

    Each inverter dict will have 'sn' (16-char), 'device_id' (first 8), 'device_serial' (last 8).
    """
    session = async_get_clientsession(hass)
    try:
        resp = await session.post(
            SEMS_MONITOR_URL,
            json={"powerStationId": station_id},
            headers={
                "Content-Type": "application/json",
                "token": json.dumps(token_data),
            },
            timeout=15,
        )
        data = await resp.json(content_type=None)
    except Exception:
        return None, "cannot_connect"

    if data.get("code") not in (0, "0"):
        return None, "cannot_fetch_inverters"

    raw_inverters = data.get("data", {}).get("inverter", [])
    if isinstance(raw_inverters, dict):
        raw_inverters = [raw_inverters]

    inverters = []
    for inv in raw_inverters:
        sn = inv.get("sn", "")
        if len(sn) >= 16:
            device_id = sn[:8]
            device_serial = sn[8:16]
        else:
            device_id = sn
            device_serial = ""
        inverters.append({
            "sn": sn,
            "device_id": device_id,
            "device_serial": device_serial,
            "name": inv.get("name") or inv.get("model") or sn,
        })

    if not inverters:
        return None, "no_inverters"

    return inverters, None


class GoodweLocalSemsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GoodWe Local SEMS Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._stations: list[dict] = []
        self._inverters: list[dict] = []
        self._token_data: dict = {}
        self._prefetched_station_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select Goodwe integration."""
        goodwe_entries = self.hass.config_entries.async_entries("goodwe")

        if not goodwe_entries:
            return self.async_abort(reason="no_goodwe_integration")

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
        """Step to enter SEMS credentials. Authenticates and fetches station list."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            token_data, stations, error_key = await _sems_fetch_stations(
                self.hass, username, password
            )

            if error_key:
                errors["base"] = error_key
            elif not stations:
                errors["base"] = "no_stations"
            else:
                self.context["sems_data"] = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                }
                self._stations = stations
                self._token_data = token_data or {}

                # Pre-fetch inverters for the first station so defaults cascade
                first_station_id = stations[0]["id"]
                inverters, inv_error = await _sems_fetch_inverters(
                    self.hass, self._token_data, first_station_id
                )
                if not inv_error and inverters:
                    self._inverters = inverters
                    self._prefetched_station_id = first_station_id

                return await self.async_step_select_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

        return self.async_show_form(
            step_id="sems_credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick a SEMS power station (defaults to first). Then fetch its inverters."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_id = user_input[CONF_SEMS_STATION_ID]
            self.context["sems_data"][CONF_SEMS_STATION_ID] = station_id

            # Reuse pre-fetched inverters if user kept the default station
            if station_id == self._prefetched_station_id and self._inverters:
                return await self.async_step_select_inverter()

            # Otherwise fetch inverters for the newly chosen station
            inverters, error_key = await _sems_fetch_inverters(
                self.hass, self._token_data, station_id
            )
            if error_key:
                errors["base"] = error_key
            else:
                self._inverters = inverters or []
                return await self.async_step_select_inverter()

        station_options = {
            s["id"]: f"{s.get('name', s['id'])} ({s['id']})"
            for s in self._stations
        }
        first_station_id = self._stations[0]["id"] if self._stations else None

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SEMS_STATION_ID,
                    default=first_station_id,
                ): vol.In(station_options),
            }
        )

        return self.async_show_form(
            step_id="select_station",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_select_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick an inverter — device_id and device_serial are derived from its SN."""
        CONF_INVERTER_SN = "inverter_sn"

        if user_input is not None:
            chosen_sn = user_input[CONF_INVERTER_SN]
            inv = next((i for i in self._inverters if i["sn"] == chosen_sn), None)
            if inv:
                self.context["sems_data"][CONF_DEVICE_ID] = inv["device_id"]
                self.context["sems_data"][CONF_DEVICE_SERIAL] = inv["device_serial"]
                _LOGGER.info(
                    "Selected inverter SN %s → device_id=%s device_serial=%s",
                    chosen_sn, inv["device_id"], inv["device_serial"],
                )
            return await self.async_step_sync_settings()

        inverter_options = {
            i["sn"]: f"{i['name']} (SN: {i['sn']})"
            for i in self._inverters
        }
        first_inv = self._inverters[0] if self._inverters else None
        first_sn = first_inv["sn"] if first_inv else None

        schema = vol.Schema(
            {
                vol.Required(CONF_INVERTER_SN, default=first_sn): vol.In(inverter_options),
            }
        )

        description_placeholders = {}
        if first_inv:
            description_placeholders = {
                "device_id": first_inv["device_id"],
                "device_serial": first_inv["device_serial"],
            }

        return self.async_show_form(
            step_id="select_inverter",
            data_schema=schema,
            description_placeholders=description_placeholders,
        )

    async def async_step_sync_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to configure sync settings."""
        if user_input is not None:
            self.context["sync_data"] = user_input
            return await self.async_step_aa55_settings()

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

    async def async_step_aa55_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to configure AA55 MITM proxy settings."""
        if user_input is not None:
            goodwe_entry_id = self.context.get("goodwe_entry_id")
            sems_data = self.context.get("sems_data", {})
            sync_data = self.context.get("sync_data", {})
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
                    **sync_data,
                    **user_input,
                    CONF_GOODWE_ENTRY_ID: goodwe_entry_id,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_AA55_PROXY_ENABLED, default=False
                ): cv.boolean,
                vol.Optional(
                    CONF_AA55_PROXY_PORT, default=DEFAULT_AA55_PROXY_PORT
                ): cv.port,
            }
        )

        return self.async_show_form(
            step_id="aa55_settings",
            data_schema=schema,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(import_data)
