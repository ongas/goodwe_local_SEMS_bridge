"""SEMS sync relay for the GoodWe Local SEMS Bridge integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class GoodweLocalSemsRelay:
    """Simple relay that syncs Goodwe data to SEMS API.
    
    Reads data from the official Goodwe integration and syncs to SEMS API
    once per minute (regardless of local read frequency).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        goodwe_entry_id: str,
        sems_username: str,
        sems_password: str,
        sems_station_id: str,
    ) -> None:
        """Initialize the relay."""
        self.hass = hass
        self.goodwe_entry_id = goodwe_entry_id
        self.sems_username = sems_username
        self.sems_password = sems_password
        self.sems_station_id = sems_station_id
        
        self._sems_api: Any = None
        self._last_sems_sync: datetime | None = None
        self._sems_sync_failed = False
        
        # Initialize SEMS API
        self._init_sems_api()

    def _init_sems_api(self) -> None:
        """Initialize the SEMS API client."""
        try:
            from custom_components.sems.sems_api import SemsApi
            
            self._sems_api = SemsApi(
                self.hass,
                self.sems_username,
                self.sems_password,
            )
            
            # Test authentication
            if not self._sems_api.test_authentication():
                _LOGGER.warning("SEMS API authentication failed")
                self._sems_api = None
                self._sems_sync_failed = True
            else:
                _LOGGER.info("SEMS API initialized successfully")
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to initialize SEMS API: %s", ex)
            self._sems_api = None

    async def async_sync(self) -> bool:
        """Sync latest Goodwe data to SEMS.
        
        Returns True if sync succeeded, False otherwise.
        """
        if not self._sems_api:
            if not self._sems_sync_failed:
                # First-time failure, try to reinitialize
                self._init_sems_api()
            return False
        
        try:
            # Get the Goodwe integration's data
            goodwe_runtime_data = self.hass.data.get("goodwe", {}).get(
                self.goodwe_entry_id
            )
            
            if not goodwe_runtime_data:
                _LOGGER.warning("Goodwe integration not found")
                return False
            
            goodwe_coordinator = goodwe_runtime_data.coordinator
            
            if goodwe_coordinator.data is None:
                _LOGGER.debug("No data from Goodwe coordinator yet")
                return False
            
            # TODO: Implement actual SEMS sync logic
            # This will push the relevant fields from goodwe_coordinator.data to SEMS
            _LOGGER.debug("Syncing data to SEMS API")
            self._last_sems_sync = datetime.now()
            self._sems_sync_failed = False
            return True
            
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("SEMS sync failed: %s", ex)
            self._sems_sync_failed = True
            return False

    def get_status(self) -> dict[str, Any]:
        """Get the current sync status."""
        return {
            "api_initialized": self._sems_api is not None,
            "last_sync": self._last_sems_sync,
            "failed": self._sems_sync_failed,
        }
