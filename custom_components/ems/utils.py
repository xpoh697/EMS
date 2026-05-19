"""General utilities for the EMS integration."""
import os
import logging
from logging.handlers import RotatingFileHandler
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

def _setup_handler_executor(log_dir: str, log_file: str) -> RotatingFileHandler:
    """Create directory and RotatingFileHandler in the executor thread."""
    os.makedirs(log_dir, exist_ok=True)
    return RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8"
    )

async def setup_ems_logger(hass: HomeAssistant) -> None:
    """Set up the custom logger with RotatingFileHandler for EMS integration."""
    logger = logging.getLogger("custom_components.ems")
    
    # Disable propagation to prevent writing to home-assistant.log
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    # Clean up existing RotatingFileHandlers to avoid duplicates during reload
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            handler.close()
            logger.removeHandler(handler)

    log_dir = hass.config.path("custom_components", "ems")
    log_file = os.path.join(log_dir, "ems.log")

    # Run blocking file/handler setup in the executor thread pool
    handler = await hass.async_add_executor_job(
        _setup_handler_executor,
        log_dir,
        log_file
    )

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    
    logger.addHandler(handler)
