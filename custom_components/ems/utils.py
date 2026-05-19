"""General utilities for the EMS integration."""
import logging
from homeassistant.core import HomeAssistant
from .const import DOMAIN, CONF_DEBUG

def is_debug_enabled(hass: HomeAssistant) -> bool:
    """Check if debug logging is enabled for the EMS integration."""
    if DOMAIN in hass.data:
        return hass.data[DOMAIN].get("debug", False)
    return False

def ems_log(
    hass: HomeAssistant,
    logger: logging.Logger,
    level: int,
    message: str,
    *args,
    **kwargs
) -> None:
    """Write log messages respecting the user-configured debug flag."""
    if level == logging.DEBUG:
        if is_debug_enabled(hass):
            logger.debug(message, *args, **kwargs)
    else:
        logger.log(level, message, *args, **kwargs)
