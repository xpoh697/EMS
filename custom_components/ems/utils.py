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
        # For WARNING and above, also write to standard Home Assistant log
        if level in (logging.WARNING, logging.ERROR, logging.CRITICAL):
            std_logger = logging.getLogger(f"homeassistant.{logger.name}")
            std_logger.log(level, message, *args, **kwargs)

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

def parse_price_sensor(state_obj) -> tuple[list[float], list[float], bool, bool]:
    """Parse hourly prices for today and tomorrow from a price sensor.

    Returns:
        (price_today, price_tomorrow, today_has_data, tomorrow_has_data)

        The *_has_data flags are True when the corresponding sensor attribute
        exists as a list — regardless of whether the values are zero or negative.
        This lets callers distinguish "no data received yet" from
        "data received but prices happen to be zero/negative" (valid Nordpool case).
    """
    price_today = [0.0] * 24
    price_tomorrow = [0.0] * 24
    today_has_data = False
    tomorrow_has_data = False

    if not state_obj or not state_obj.attributes:
        return price_today, price_tomorrow, today_has_data, tomorrow_has_data

    attrs = state_obj.attributes

    from datetime import timedelta
    now = dt_util.now()
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()

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
                        if local_dt.date() == today_date:
                            hour = local_dt.hour
                            if 0 <= hour < 24:
                                price_today[hour] = round(float(price_val), 6)
                                today_has_data = True  # at least one price for today parsed
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
                        if local_dt.date() == tomorrow_date:
                            hour = local_dt.hour
                            if 0 <= hour < 24:
                                price_tomorrow[hour] = round(float(price_val), 6)
                                tomorrow_has_data = True  # at least one price for tomorrow parsed
                except Exception:
                    pass

    return price_today, price_tomorrow, today_has_data, tomorrow_has_data

def map_dp_to_physical(
    action: str | None,
    sell_price: float,
    pv_kwh: float,
    min_sell_price: float,
    min_discharge_price: float,
    cheap_ahead: bool,
) -> tuple[str | None, str]:
    """Map a DP algorithmic action to a physical inverter mode, returning (mode, reason)."""
    price_cond = "sell_price > min_sell_price" if sell_price > min_sell_price else "sell_price <= min_sell_price"
    discharge_price_cond = "sell_price >= min_discharge_price" if sell_price >= min_discharge_price else "sell_price < min_discharge_price"
    pv_cond = "pv_kwh > 0.01" if pv_kwh > 0.01 else "pv_kwh <= 0.01"
    cheap_cond = f"cheap_ahead={cheap_ahead}"
    reason = f"{price_cond} | {discharge_price_cond} | {pv_cond} | {cheap_cond}"

    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency"):
        return action, "direct_mapping"

    # Direct mapping for idle
    if action == "idle":
        return "idle", f"idle_bypass | {reason}"

    if action == "discharge":
        if sell_price >= min_discharge_price:
            return "sale_pv_bat", reason
        return "stop_sale", reason

    if action in ("grid_charge", "paid_import"):
        return "buy", reason

    # Actions: pv_charge, self_consume, solar_export
    if sell_price > min_sell_price:
        if action == "solar_export" and pv_kwh > 0.01 and sell_price >= min_discharge_price:
            return "sale_pv_no_bat", reason
        return "sale_pv", reason

    # sell_price <= min_sell_price
    if cheap_ahead:
        return "no_pv_sale_no_bat", reason
    return "stop_sale", reason


def map_override_to_physical(
    action: str | None,
    sell_price: float,
    pv_kwh: float,
    min_sell_price: float,
    min_discharge_price: float,
    cheap_ahead: bool,
) -> tuple[str | None, str]:
    """Map a MANUAL override action to a physical inverter mode.

    Differs from map_dp_to_physical in that 'discharge' always maps to
    'sale_pv_bat' regardless of sell_price — this prevents solar surplus
    from charging the battery during a forced-discharge override.
    """
    price_cond = "sell_price > min_sell_price" if sell_price > min_sell_price else "sell_price <= min_sell_price"
    discharge_price_cond = "sell_price >= min_discharge_price" if sell_price >= min_discharge_price else "sell_price < min_discharge_price"
    pv_cond = "pv_kwh > 0.01" if pv_kwh > 0.01 else "pv_kwh <= 0.01"
    cheap_cond = f"cheap_ahead={cheap_ahead}"
    reason = f"{price_cond} | {discharge_price_cond} | {pv_cond} | {cheap_cond}"

    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency"):
        return action, "direct_mapping"

    if action == "idle":
        return "idle", f"idle_bypass | {reason}"

    # For manual overrides: discharge always forces sale_pv_bat
    # (disables charge_from_pv, enables discharge_to_grid/discharge_to_house)
    if action == "discharge":
        return "sale_pv_bat", f"override_discharge_forced | {reason}"

    if action in ("grid_charge", "paid_import"):
        return "buy", reason

    if sell_price > min_sell_price:
        if action == "solar_export" and pv_kwh > 0.01 and sell_price >= min_discharge_price:
            return "sale_pv_no_bat", reason
        return "sale_pv", reason

    if cheap_ahead:
        return "no_pv_sale_no_bat", reason
    return "stop_sale", reason
