"""The Energy Management System (EMS) integration."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_DEBUG
from .utils import setup_ems_logger

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EMS from a config entry."""
    # Set up custom rotating file logger for the integration
    await setup_ems_logger(hass)

    hass.data.setdefault(DOMAIN, {})
    
    # Store settings in memory
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    # Cache debug flag for fast utility access
    debug_enabled = entry.options.get(CONF_DEBUG, entry.data.get(CONF_DEBUG, False))
    hass.data[DOMAIN]["debug"] = debug_enabled

    # Register options update listener to reload when settings change
    entry.async_on_unload(entry.add_update_listener(async_update_options_listener))
    
    # Forward entry setups to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok and entry.entry_id in hass.data.get(DOMAIN, {}):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def async_update_options_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update and reload the integration."""
    _LOGGER.debug("Options updated, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)
