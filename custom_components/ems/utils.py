"""General utilities for the EMS integration."""
import os
import logging
from logging.handlers import RotatingFileHandler
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .const import DOMAIN, CONF_DEBUG, PRICE_MODE_FIXED, PRICE_MODE_SENSOR

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

    # Parse PradCast format: attribute 'prices' with list of {'hour': ..., 'price_kwh': ...}
    prices_data = attrs.get("prices")
    if isinstance(prices_data, list):
        sensor_date_str = str(attrs.get("date", ""))[:10]
        entity_id = getattr(state_obj, "entity_id", "")
        
        if sensor_date_str:
            is_tomorrow = (sensor_date_str == str(tomorrow_date))
            is_today = (sensor_date_str == str(today_date))
        else:
            is_tomorrow = ("jutro" in entity_id) or ("tomorrow" in entity_id)
            is_today = ("dzis" in entity_id) or ("today" in entity_id)

        for item in prices_data:
            if not isinstance(item, dict):
                continue
            h = item.get("hour")
            p = item.get("price_kwh")
            if p is None and item.get("price") is not None:
                try:
                    p = float(item["price"]) / 1000.0
                except (ValueError, TypeError):
                    p = None
            if h is not None and p is not None and 0 <= int(h) < 24:
                val = round(float(p), 6)
                if is_tomorrow:
                    price_tomorrow[int(h)] = val
                    tomorrow_has_data = True
                elif is_today:
                    price_today[int(h)] = val
                    today_has_data = True

    return price_today, price_tomorrow, today_has_data, tomorrow_has_data

def resolve_hourly_tariffs(
    *,
    buy_mode: str,
    buy_fixed: float | None,
    buy_state,
    sell_mode: str,
    sell_fixed: float | None,
    sell_state,
    prad_today_state=None,
    prad_tomorrow_state=None,
    distribution_fee_fallback: float = 0.0,
    prev_buy_tomorrow: list[float] | None = None,
    prev_sell_tomorrow: list[float] | None = None,
) -> dict:
    """Resolve 24h today/tomorrow buy & sell tariffs for sensor, fixed, or hybrid modes."""
    buy_rollover_used = False
    sell_rollover_used = False

    if buy_mode == PRICE_MODE_FIXED:
        fixed_b = round(float(buy_fixed if buy_fixed is not None else 0.0), 6)
        buy_today = [fixed_b] * 24
        buy_tomorrow = [fixed_b] * 24
        buy_today_has_data = True
        buy_tomorrow_has_data = True
        buy_today_source = "fixed"
        buy_tomorrow_source = "fixed"
    else:
        buy_today, buy_tomorrow, buy_today_has_data, buy_tomorrow_has_data = parse_price_sensor(buy_state)
        if not buy_today_has_data and prev_buy_tomorrow and any(v != 0.0 for v in prev_buy_tomorrow):
            buy_today = list(prev_buy_tomorrow)
            buy_rollover_used = True
        buy_today_source = "pstryk" if (buy_today_has_data and any(v != 0.0 for v in buy_today)) else "none"
        buy_tomorrow_source = "pstryk" if (buy_tomorrow_has_data and any(v != 0.0 for v in buy_tomorrow)) else "none"

    if sell_mode == PRICE_MODE_FIXED:
        fixed_s = round(float(sell_fixed if sell_fixed is not None else 0.0), 6)
        sell_today = [fixed_s] * 24
        sell_tomorrow = [fixed_s] * 24
        sell_today_has_data = True
        sell_tomorrow_has_data = True
        sell_today_source = "fixed"
        sell_tomorrow_source = "fixed"
    else:
        sell_today, sell_tomorrow, sell_today_has_data, sell_tomorrow_has_data = parse_price_sensor(sell_state)
        if not sell_today_has_data and prev_sell_tomorrow and any(v != 0.0 for v in prev_sell_tomorrow):
            sell_today = list(prev_sell_tomorrow)
            sell_rollover_used = True
        sell_today_source = "pstryk" if (sell_today_has_data and any(v != 0.0 for v in sell_today)) else "none"
        sell_tomorrow_source = "pstryk" if (sell_tomorrow_has_data and any(v != 0.0 for v in sell_tomorrow)) else "none"

    prad_td: list[float] = []
    has_prad_td = False
    if prad_today_state is not None:
        prad_td, _, has_prad_td, _ = parse_price_sensor(prad_today_state)

    # PradCast Today fallback (only for sensor-mode channels without Pstryk today data)
    if (
        (buy_mode == PRICE_MODE_SENSOR and buy_today_source != "pstryk")
        or (sell_mode == PRICE_MODE_SENSOR and sell_today_source != "pstryk")
    ):
        if has_prad_td and any(v != 0.0 for v in prad_td):
            forecast_sell_td = [round(prad_td[h] * 1.23, 6) for h in range(24)]
            forecast_buy_td = [round(max(0.0, forecast_sell_td[h] + distribution_fee_fallback), 6) for h in range(24)]
            if buy_mode == PRICE_MODE_SENSOR and buy_today_source != "pstryk" and not any(v != 0.0 for v in buy_today):
                buy_today = forecast_buy_td
                buy_today_has_data = True
                buy_today_source = "pradcast_forecast"
            if sell_mode == PRICE_MODE_SENSOR and sell_today_source != "pstryk" and not any(v != 0.0 for v in sell_today):
                sell_today = forecast_sell_td
                sell_today_has_data = True
                sell_today_source = "pradcast_forecast"

    # PradCast Tomorrow fallback (only for sensor-mode channels without Pstryk tomorrow data)
    if prad_tomorrow_state is not None and (
        (buy_mode == PRICE_MODE_SENSOR and buy_tomorrow_source != "pstryk")
        or (sell_mode == PRICE_MODE_SENSOR and sell_tomorrow_source != "pstryk")
    ):
        _, prad_tmr, _, has_prad_tmr = parse_price_sensor(prad_tomorrow_state)
        if has_prad_tmr and any(v != 0.0 for v in prad_tmr):
            cur_sell_today = list(sell_today)
            if (not cur_sell_today or not any(cur_sell_today)) and has_prad_td and any(prad_td):
                cur_sell_today = [round(p * 1.23, 6) for p in prad_td]

            diffs = []
            hourly_distrib = [distribution_fee_fallback] * 24
            for h in range(24):
                b = buy_today[h] if h < len(buy_today) else 0.0
                s = cur_sell_today[h] if h < len(cur_sell_today) else 0.0
                if b > 0.0 and s > 0.0 and b > s:
                    d = round(b - s, 4)
                    diffs.append(d)
                    hourly_distrib[h] = d

            if diffs:
                diffs_sorted = sorted(diffs)
                median_d = diffs_sorted[len(diffs_sorted) // 2]
                for h in range(24):
                    if hourly_distrib[h] == distribution_fee_fallback:
                        hourly_distrib[h] = median_d

            forecast_sell = [round(prad_tmr[h] * 1.23, 6) for h in range(24)]
            forecast_buy = [round(max(0.0, forecast_sell[h] + hourly_distrib[h]), 6) for h in range(24)]

            if buy_mode == PRICE_MODE_SENSOR and buy_tomorrow_source != "pstryk":
                buy_tomorrow = forecast_buy
                buy_tomorrow_has_data = True
                buy_tomorrow_source = "pradcast_forecast"
            if sell_mode == PRICE_MODE_SENSOR and sell_tomorrow_source != "pstryk":
                sell_tomorrow = forecast_sell
                sell_tomorrow_has_data = True
                sell_tomorrow_source = "pradcast_forecast"

    def _combine_sources(b_src: str, s_src: str) -> str:
        if "pradcast_forecast" in (b_src, s_src):
            return "pradcast_forecast"
        if "pstryk" in (b_src, s_src):
            return "pstryk"
        if "fixed" in (b_src, s_src):
            return "fixed"
        return "none"

    today_source = _combine_sources(buy_today_source, sell_today_source)
    tomorrow_source = _combine_sources(buy_tomorrow_source, sell_tomorrow_source)
    # Apply forecast sell discount only when tomorrow sell price itself is a PradCast forecast
    tomorrow_is_forecast = (sell_tomorrow_source == "pradcast_forecast")
    price_tomorrow_has_data = buy_tomorrow_has_data and sell_tomorrow_has_data

    return {
        "buy_today": buy_today,
        "buy_tomorrow": buy_tomorrow,
        "sell_today": sell_today,
        "sell_tomorrow": sell_tomorrow,
        "buy_today_has_data": buy_today_has_data,
        "buy_tomorrow_has_data": buy_tomorrow_has_data,
        "sell_today_has_data": sell_today_has_data,
        "sell_tomorrow_has_data": sell_tomorrow_has_data,
        "price_tomorrow_has_data": price_tomorrow_has_data,
        "today_source": today_source,
        "tomorrow_source": tomorrow_source,
        "tomorrow_is_forecast": tomorrow_is_forecast,
        "buy_rollover_used": buy_rollover_used,
        "sell_rollover_used": sell_rollover_used,
    }

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

    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency", "OffGrid", "offgrid"):
        return action, "direct_mapping"

    # Direct mapping for idle
    if action == "idle":
        return "idle", f"idle_bypass | {reason}"

    if action == "discharge":
        if sell_price >= min_discharge_price:
            return "sale_pv_bat", reason
        # If sell_price dropped below min_discharge_price (e.g. user raised threshold),
        # fall through to normal sell_price / min_sell_price mapping (sale_pv vs stop_sale).

    if action in ("grid_charge", "paid_import"):
        return "buy", reason

    # Actions: pv_charge, self_consume, normal, solar_export
    if sell_price > min_sell_price:
        if action == "solar_export" and pv_kwh > 0.01 and sell_price > min_sell_price:
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

    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency", "OffGrid", "offgrid"):
        return action, "direct_mapping"

    if action == "idle":
        return "idle", f"idle_bypass | {reason}"

    # For manual overrides: discharge always forces sale_pv_bat
    # (disables charge_from_pv, enables discharge_to_grid/discharge_to_house)
    if action == "discharge":
        return "sale_pv_bat", f"override_discharge_forced | {reason}"

    if action in ("grid_charge", "paid_import"):
        return "buy", reason

    # Actions: pv_charge, self_consume, normal, solar_export
    if sell_price > min_sell_price:
        if action == "solar_export" and pv_kwh > 0.01 and sell_price > min_sell_price:
            return "sale_pv_no_bat", reason
        return "sale_pv", reason

    if cheap_ahead:
        return "no_pv_sale_no_bat", reason
    return "stop_sale", reason
