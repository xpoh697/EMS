"""Services for the Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_OVERRIDE = "set_manual_override"
SERVICE_CLEAR_OVERRIDE = "clear_manual_override"
SERVICE_CLEAR_ALL_OVERRIDES = "clear_all_overrides"

SET_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    vol.Required("action"): vol.In(["grid_charge", "discharge", "self_consume", "idle"]),
})

CLEAR_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
})


async def async_setup_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up EMS services."""
    storage = hass.data[DOMAIN][entry.entry_id]["storage"]

    async def handle_set_override(call: ServiceCall) -> None:
        """Handle setting a manual override."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        action = call.data["action"]
        await storage.async_set_override(date_str, hour, action)
        _LOGGER.debug("Manual override set via service: %s %02d:00 -> %s", date_str, hour, action)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_override(call: ServiceCall) -> None:
        """Handle clearing a manual override."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        await storage.async_clear_override(date_str, hour)
        _LOGGER.debug("Manual override cleared via service: %s %02d:00", date_str, hour)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_all_overrides(call: ServiceCall) -> None:
        """Handle clearing all manual overrides."""
        await storage.async_clear_all_overrides()
        _LOGGER.debug("All manual overrides cleared via service")
        hass.bus.async_fire("ems_schedule_updated")

    hass.services.async_register(
        DOMAIN, SERVICE_SET_OVERRIDE, handle_set_override, schema=SET_OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_OVERRIDE, handle_clear_override, schema=CLEAR_OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_ALL_OVERRIDES, handle_clear_all_overrides
    )
    _LOGGER.debug("EMS services successfully registered")


def async_unload_services(hass: HomeAssistant) -> None:
    """Unload EMS services."""
    hass.services.async_remove(DOMAIN, SERVICE_SET_OVERRIDE)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_OVERRIDE)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_ALL_OVERRIDES)
    _LOGGER.debug("EMS services unregistered")
