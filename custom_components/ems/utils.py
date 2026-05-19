"""General utilities for the EMS integration."""
import os
import logging
from logging.handlers import RotatingFileHandler
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
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

    log_dir = hass.config.config_dir
    log_file = hass.config.path("ems.log")

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

def calculate_battery_degradation(price: float, cycles: float | int, capacity: float) -> float:
    """Calculate battery degradation cost per kWh based on price, cycles, and capacity."""
    if cycles <= 0 or capacity <= 0.0:
        return 0.0
    total_throughput = float(cycles) * float(capacity)
    if total_throughput <= 0.0:
        return 0.0
    return round(float(price) / total_throughput, 6)

def parse_price_sensor(state_obj) -> tuple[list[float], list[float]]:
    """Parse hourly prices for today and tomorrow from a price sensor."""
    price_today = [0.0] * 24
    price_tomorrow = [0.0] * 24
    if not state_obj or not state_obj.attributes:
        return price_today, price_tomorrow

    attrs = state_obj.attributes

    # Parse today's prices
    today_data = attrs.get("price_today")
    if isinstance(today_data, list):
        for item in today_data:
            if not isinstance(item, dict):
                continue
            start_str = item.get("start")
            price_val = item.get("price")
            if start_str and price_val is not None:
                try:
                    parsed_dt = dt_util.parse_datetime(start_str)
                    if parsed_dt:
                        local_dt = dt_util.as_local(parsed_dt)
                        hour = local_dt.hour
                        if 0 <= hour < 24:
                            price_today[hour] = round(float(price_val), 6)
                except Exception:
                    pass

    # Parse tomorrow's prices
    tomorrow_data = attrs.get("price_tomorrow")
    if isinstance(tomorrow_data, list):
        for item in tomorrow_data:
            if not isinstance(item, dict):
                continue
            start_str = item.get("start")
            price_val = item.get("price")
            if start_str and price_val is not None:
                try:
                    parsed_dt = dt_util.parse_datetime(start_str)
                    if parsed_dt:
                        local_dt = dt_util.as_local(parsed_dt)
                        hour = local_dt.hour
                        if 0 <= hour < 24:
                            price_tomorrow[hour] = round(float(price_val), 6)
                except Exception:
                    pass

    return price_today, price_tomorrow
