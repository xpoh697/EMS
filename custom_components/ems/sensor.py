"""Sensor platform for EMS integration."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    VERSION,
    CONF_TOTAL_LOAD_CONSUMPTION,
    CONF_CURRENT_HOUSE_CONSUMPTION,
    CONF_INVERTER_MODES_LIST,
    CONF_CURRENT_PV_GENERATION,
    CONF_PV_GENERATION_TODAY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    CONF_DEBUG,
    CONF_PRICE_BUY_SENSOR,
    CONF_PRICE_SELL_SENSOR,
    CONF_SYSTEM_COST,
    CONF_MIN_SELL_PRICE,
    CONF_BAT_PRICE,
    CONF_BAT_CYCLES,
    CONF_BAT_CAPACITY_ENTITY,
    CONF_BAT_MAX_POWER,
    CONF_BAT_CUR_POWER_ENTITY,
    CONF_BAT_SOC_ENTITY,
    CONF_BAT_VOLTAGE,
    CONF_MIN_BAT_SOC,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_DEBUG,
    DEFAULT_SYSTEM_COST,
    DEFAULT_MIN_SELL_PRICE,
    DEFAULT_BAT_PRICE,
    DEFAULT_BAT_CYCLES,
    DEFAULT_BAT_MAX_POWER,
    DEFAULT_MIN_BAT_SOC,
    SOC_HYSTERESIS,
)
from .utils import ems_log, calculate_battery_degradation, parse_price_sensor
from .dp_engine import run_unified_dp, DPConfig

_LOGGER = logging.getLogger(__name__)

def safe_float_list(val, default_val, length=24) -> list[float]:
    """Ensure a list is a 24-element float list, applying default values where needed."""
    if not isinstance(val, list):
        return [default_val] * length
    cleaned = []
    for x in val:
        try:
            cleaned.append(float(x) if x is not None else default_val)
        except (ValueError, TypeError):
            cleaned.append(default_val)
    if len(cleaned) < length:
        cleaned.extend([default_val] * (length - len(cleaned)))
    return cleaned[:length]

def map_dp_to_physical(
    action: str | None,
    sell_price: float,
    pv_kwh: float,
    min_sell_price: float,
    cheap_ahead: bool,
) -> tuple[str | None, str]:
    """Map a DP algorithmic action to a physical inverter mode, returning (mode, reason)."""
    price_cond = "sell_price > min_sell_price" if sell_price > min_sell_price else "sell_price <= min_sell_price"
    pv_cond = "pv_kwh > 0.01" if pv_kwh > 0.01 else "pv_kwh <= 0.01"
    cheap_cond = f"cheap_ahead={cheap_ahead}"
    reason = f"{price_cond} | {pv_cond} | {cheap_cond}"

    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency"):
        return action, "direct_mapping"

    # Direct mapping for idle
    if action == "idle":
        return "idle", f"idle_bypass | {reason}"

    if action == "discharge":
        if sell_price > min_sell_price:
            return "sale_pv_bat", reason
        return "stop_sale", reason

    if action in ("grid_charge", "paid_import"):
        return "buy", reason

    # Actions: pv_charge, self_consume, solar_export
    if sell_price > min_sell_price:
        if action == "solar_export" and pv_kwh > 0.01:
            return "sale_pv_no_bat", reason
        return "sale_pv", reason

    # sell_price <= min_sell_price
    if cheap_ahead:
        return "no_pv_sale_no_bat", reason
    return "stop_sale", reason

def parse_solcast_forecast(hass: HomeAssistant, state_obj) -> tuple[list[float], bool]:
    """Parse Solcast detailedForecast, detailedHourly, and forecasts attributes to compute baseline hourly kWh."""
    hourly_baselines = [0.0] * 24
    if not state_obj:
        return hourly_baselines, False

    attrs = state_obj.attributes
    forecast_list = None
    matched_attr = None

    # Check for different Solcast forecast list attributes
    for attr_name in ("detailedForecast", "detailedHourly", "forecasts"):
        val = attrs.get(attr_name)
        if isinstance(val, list) and len(val) > 0:
            forecast_list = val
            matched_attr = attr_name
            break

    if forecast_list is None:
        # Fallback: if no detailed forecast attribute is present, check if there's a simple state value
        try:
            total_val = float(state_obj.state)
        except (ValueError, TypeError):
            total_val = 0.0

        # Log a warning to notify the user that fallback is being used
        ems_log(
            hass,
            _LOGGER,
            logging.WARNING,
            f"Detailed PV forecast attributes not found on {state_obj.entity_id}. "
            f"Using hardcoded solar bell curve to distribute daily total of {total_val} kWh."
        )

        # Distribute total_val over daylight hours using a solar bell curve (sum of weights = 1.0)
        solar_weights = {
            6: 0.02, 7: 0.05, 8: 0.08, 9: 0.10, 10: 0.12, 11: 0.13,
            12: 0.13, 13: 0.12, 14: 0.10, 15: 0.07, 16: 0.04, 17: 0.02,
            18: 0.02,
        }
        if total_val > 0.0:
            for h in range(24):
                weight = solar_weights.get(h, 0.0)
                hourly_baselines[h] = round(total_val * weight, 4)
        return hourly_baselines, True

    analysis = attrs.get("analysis", {})
    intervals = []
    if isinstance(analysis, dict):
        intervals = analysis.get("intervals", [])

    # Group slots by local hour
    # Default slot duration: 1.0 for hourly forecast, 0.5 for half-hourly
    slot_duration = 1.0 if matched_attr == "detailedHourly" else 0.5
    if len(forecast_list) > 0:
        # Try to calculate duration dynamically from the first two periods
        try:
            first_slot = forecast_list[0]
            if isinstance(first_slot, dict):
                start_str = first_slot.get("period_start")
                if len(forecast_list) > 1:
                    second_slot = forecast_list[1]
                    if isinstance(second_slot, dict):
                        next_start_str = second_slot.get("period_start")
                        start_dt = dt_util.parse_datetime(start_str)
                        next_dt = dt_util.parse_datetime(next_start_str)
                        if start_dt and next_dt:
                            calculated_duration = abs((next_dt - start_dt).total_seconds()) / 3600.0
                            if calculated_duration > 0.0:
                                slot_duration = calculated_duration
        except Exception:
            pass

    for i, slot in enumerate(forecast_list):
        if not isinstance(slot, dict):
            continue

        period_start = slot.get("period_start")
        if not period_start:
            continue

        try:
            parsed_dt = dt_util.parse_datetime(period_start)
            if not parsed_dt:
                continue
            local_dt = dt_util.as_local(parsed_dt)
            local_hour = local_dt.hour
        except Exception:
            if len(forecast_list) == 48:
                local_hour = min(23, max(0, i // 2))
            elif len(forecast_list) == 24:
                local_hour = min(23, max(0, i))
            else:
                continue

        try:
            pv_estimate = float(slot.get("pv_estimate", 0.0))
        except (ValueError, TypeError):
            pv_estimate = 0.0

        try:
            pv_estimate10 = float(slot.get("pv_estimate10", slot.get("estimate10", pv_estimate)))
        except (ValueError, TypeError):
            pv_estimate10 = 0.0

        # Retrieve confidence
        confidence = 1.0
        if isinstance(intervals, list) and i < len(intervals):
            confidence_val = intervals[i].get("confidence", 1.0)
        else:
            confidence_val = analysis.get("confidence", 1.0)

        try:
            confidence = float(confidence_val)
        except (ValueError, TypeError):
            confidence = 1.0

        # Baseline formula: baseline_kwh = pv_estimate10 + confidence * (pv_estimate - pv_estimate10)
        # Note: estimate is in kW, so energy is power * duration
        baseline_kw = pv_estimate10 + confidence * (pv_estimate - pv_estimate10)
        baseline_kwh = baseline_kw * slot_duration

        if 0 <= local_hour < 24:
            hourly_baselines[local_hour] += baseline_kwh

    return [round(val, 4) for val in hourly_baselines], False



async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EMS sensors from config entry."""
    config = entry.data
    options = entry.options

    # Basic settings
    target_sensor_id = options.get(CONF_TOTAL_LOAD_CONSUMPTION, config.get(CONF_TOTAL_LOAD_CONSUMPTION))
    current_house_consumption_id = options.get(CONF_CURRENT_HOUSE_CONSUMPTION, config.get(CONF_CURRENT_HOUSE_CONSUMPTION))
    inverter_modes_list_id = options.get(CONF_INVERTER_MODES_LIST, config.get(CONF_INVERTER_MODES_LIST))
    statistics_days = options.get(CONF_STATISTICS_DAYS, config.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS))
    fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))

    # PV forecast settings
    current_pv_generation_id = options.get(CONF_CURRENT_PV_GENERATION, config.get(CONF_CURRENT_PV_GENERATION))
    pv_generation_today_id = options.get(CONF_PV_GENERATION_TODAY, config.get(CONF_PV_GENERATION_TODAY))
    pv_today_id = options.get(CONF_PV_FORECAST_TODAY, config.get(CONF_PV_FORECAST_TODAY))
    pv_tomorrow_id = options.get(CONF_PV_FORECAST_TOMORROW, config.get(CONF_PV_FORECAST_TOMORROW))

    # Financial settings
    price_buy_sensor_id = options.get(CONF_PRICE_BUY_SENSOR, config.get(CONF_PRICE_BUY_SENSOR))
    price_sell_sensor_id = options.get(CONF_PRICE_SELL_SENSOR, config.get(CONF_PRICE_SELL_SENSOR))
    system_cost = options.get(CONF_SYSTEM_COST, config.get(CONF_SYSTEM_COST, DEFAULT_SYSTEM_COST))
    min_sell_price = options.get(CONF_MIN_SELL_PRICE, config.get(CONF_MIN_SELL_PRICE, DEFAULT_MIN_SELL_PRICE))

    # Battery optimization settings
    bat_price = options.get(CONF_BAT_PRICE, config.get(CONF_BAT_PRICE, DEFAULT_BAT_PRICE))
    bat_cycles = options.get(CONF_BAT_CYCLES, config.get(CONF_BAT_CYCLES, DEFAULT_BAT_CYCLES))
    bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
    bat_max_power = options.get(CONF_BAT_MAX_POWER, config.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER))
    bat_cur_power_entity_id = options.get(CONF_BAT_CUR_POWER_ENTITY, config.get(CONF_BAT_CUR_POWER_ENTITY))
    bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
    bat_voltage_entity_id = options.get(CONF_BAT_VOLTAGE, config.get(CONF_BAT_VOLTAGE))
    min_bat_soc = options.get(CONF_MIN_BAT_SOC, config.get(CONF_MIN_BAT_SOC, DEFAULT_MIN_BAT_SOC))

    # Log errors or info for house consumption / basic settings
    if not target_sensor_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Total load consumption sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Total load consumption sensor configured successfully: {target_sensor_id}")

    if not current_house_consumption_id:
        ems_log(hass, _LOGGER, logging.WARNING, "Current house consumption sensor is not configured in settings.")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Current house consumption sensor configured successfully: {current_house_consumption_id}")

    if not inverter_modes_list_id:
        ems_log(hass, _LOGGER, logging.WARNING, "Inverter modes list is not configured in settings.")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Inverter modes list configured successfully: {inverter_modes_list_id}")

    # Log errors or info for PV sensors
    if not current_pv_generation_id:
        ems_log(hass, _LOGGER, logging.WARNING, "Current PV generation sensor is not configured in settings.")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Current PV generation sensor configured successfully: {current_pv_generation_id}")

    if not pv_generation_today_id:
        ems_log(hass, _LOGGER, logging.WARNING, "PV generation today sensor is not configured in settings.")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"PV generation today sensor configured successfully: {pv_generation_today_id}")

    if not pv_today_id:
        ems_log(hass, _LOGGER, logging.ERROR, "PV Forecast today sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"PV Forecast today sensor configured successfully: {pv_today_id}")

    if not pv_tomorrow_id:
        ems_log(hass, _LOGGER, logging.ERROR, "PV Forecast tomorrow sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"PV Forecast tomorrow sensor configured successfully: {pv_tomorrow_id}")

    # Log errors or info for Financial sensors
    if not price_buy_sensor_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Price buy sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Price buy sensor configured successfully: {price_buy_sensor_id}")

    if not price_sell_sensor_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Price sell sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Price sell sensor configured successfully: {price_sell_sensor_id}")

    ems_log(hass, _LOGGER, logging.INFO, f"Minimum sell price configured: {min_sell_price}")

    # Log errors or info for Battery optimization sensors
    if not bat_capacity_entity_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Battery capacity entity is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Battery capacity entity configured successfully: {bat_capacity_entity_id}")

    if not bat_soc_entity_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Battery State of Charge (SOC) entity is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Battery SOC entity configured successfully: {bat_soc_entity_id}")

    if not bat_cur_power_entity_id:
        ems_log(hass, _LOGGER, logging.WARNING, "Battery current power entity is not configured in settings.")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Battery current power entity configured successfully: {bat_cur_power_entity_id}")

    if not bat_voltage_entity_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Battery voltage entity is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Battery voltage entity configured successfully: {bat_voltage_entity_id}")

    ems_log(hass, _LOGGER, logging.INFO, f"Minimum battery SOC configured: {min_bat_soc}%")

    # Calculate and log battery degradation cost per kWh
    def update_degradation_cost():
        """Fetch capacity and calculate battery degradation cost per kWh."""
        capacity = 0.0
        if bat_capacity_entity_id:
            cap_state = hass.states.get(bat_capacity_entity_id)
            if cap_state and cap_state.state not in (None, "unknown", "unavailable"):
                try:
                    capacity = float(cap_state.state)
                    # Convert to kWh if unit is Wh or capacity value seems to be in Wh
                    unit = cap_state.attributes.get("unit_of_measurement")
                    if unit == "Wh" or capacity > 100.0:
                        capacity = capacity / 1000.0
                except (ValueError, TypeError):
                    pass

        # Calculate degradation
        degradation = calculate_battery_degradation(bat_price, bat_cycles, capacity)

        # Save to domain data for future use
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        hass.data[DOMAIN]["bat_degradation_per_kwh"] = degradation

        # Log the calculated value
        ems_log(
            hass,
            _LOGGER,
            logging.INFO,
            f"Battery degradation calculated: {degradation:.6f} per kWh (Price: {bat_price}, Cycles: {bat_cycles}, Capacity: {capacity:.3f} kWh)"
        )

    # Initial calculation
    update_degradation_cost()

    # Track state changes of capacity entity if configured
    if bat_capacity_entity_id:
        async def _async_capacity_changed_listener(event):
            update_degradation_cost()

        cleanup = async_track_state_change_event(
            hass, [bat_capacity_entity_id], _async_capacity_changed_listener
        )
        entry.async_on_unload(cleanup)

    # Calculate and log buy/sell price sensors
    def update_prices():
        """Fetch and parse buy/sell prices, then save to hass.data."""
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}

        if price_buy_sensor_id:
            buy_state = hass.states.get(price_buy_sensor_id)
            buy_today, buy_tomorrow = parse_price_sensor(buy_state)
            hass.data[DOMAIN]["price_buy_today"] = buy_today
            hass.data[DOMAIN]["price_buy_tomorrow"] = buy_tomorrow
            ems_log(
                hass,
                _LOGGER,
                logging.INFO,
                f"Parsed Buy Prices Today: {buy_today}, Tomorrow: {buy_tomorrow}"
            )

        if price_sell_sensor_id:
            sell_state = hass.states.get(price_sell_sensor_id)
            sell_today, sell_tomorrow = parse_price_sensor(sell_state)
            hass.data[DOMAIN]["price_sell_today"] = sell_today
            hass.data[DOMAIN]["price_sell_tomorrow"] = sell_tomorrow
            ems_log(
                hass,
                _LOGGER,
                logging.INFO,
                f"Parsed Sell Prices Today: {sell_today}, Tomorrow: {sell_tomorrow}"
            )

    # Initial calculation
    update_prices()

    # Track state changes
    price_listeners = []
    if price_buy_sensor_id:
        async def _async_buy_price_changed(event):
            update_prices()
        price_listeners.append(
            async_track_state_change_event(hass, [price_buy_sensor_id], _async_buy_price_changed)
        )

    if price_sell_sensor_id:
        async def _async_sell_price_changed(event):
            update_prices()
        price_listeners.append(
            async_track_state_change_event(hass, [price_sell_sensor_id], _async_sell_price_changed)
        )

    for cleanup in price_listeners:
        entry.async_on_unload(cleanup)

    entities = []

    if target_sensor_id:
        entities.append(
            EmsLoadConsumptionSensor(
                entry.entry_id,
                entry.title,
                target_sensor_id,
                statistics_days,
                fallback_consumption,
            )
        )

    if pv_today_id:
        entities.append(
            EmsPvForecastTodaySensor(
                entry.entry_id,
                entry.title,
                pv_today_id,
                pv_generation_today_id,
            )
        )

    if pv_tomorrow_id:
        entities.append(
            EmsPvForecastTomorrowSensor(
                entry.entry_id,
                entry.title,
                pv_tomorrow_id,
            )
        )

    if bat_soc_entity_id and bat_capacity_entity_id:
        entities.append(
            EmsDpSensor(
                entry.entry_id,
                entry.title,
                entry,
            )
        )
        entities.append(
            EmsSchedulerSensor(
                entry.entry_id,
                entry.title,
                entry,
            )
        )

    if entities:
        async_add_entities(entities)


class EmsLoadConsumptionSensor(RestoreSensor, SensorEntity):
    """EMS sensor that tracks today's load consumption and stores weekday profiles."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        device_name: str,
        target_sensor_id: str,
        statistics_days: int,
        fallback_consumption: float,
    ) -> None:
        """Initialize the load consumption sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._target_sensor_id = target_sensor_id
        self._statistics_days = statistics_days
        self._fallback_consumption = fallback_consumption

        self._attr_name = "Load Consumption"
        self._attr_unique_id = f"{entry_id}_load_consumption"

        # Internal state tracking
        self._state: float = 0.0
        self._today_consumption: list[float] = [0.0] * 24
        self._last_total_value: float | None = None
        self._last_hour: int = dt_util.now().hour
        self._last_day: int = dt_util.now().day

        # Weekday averages mapping weekday (0-6) -> 24-element list
        self._averages: dict[int, list[float]] = {}
        for weekday in range(7):
            self._averages[weekday] = [self._fallback_consumption] * 24

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> float:
        """Return today's total consumption."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        day_keys = [
            "average_monday",
            "average_tuesday",
            "average_wednesday",
            "average_thursday",
            "average_friday",
            "average_saturday",
            "average_sunday",
        ]

        now = dt_util.now()
        today_weekday = now.weekday()

        attrs = {
            "today": self._today_consumption,
            "average_today": self._averages.get(today_weekday, [self._fallback_consumption] * 24),
            "last_total_value": self._last_total_value,
            "last_hour": self._last_hour,
            "last_day": self._last_day,
        }

        for idx, key in enumerate(day_keys):
            attrs[key] = self._averages.get(idx, [self._fallback_consumption] * 24)

        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore historical states."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            try:
                self._state = float(last_state.state)
            except (ValueError, TypeError):
                self._state = 0.0

            # Restore today's hourly values
            today_attr = last_state.attributes.get("today")
            if isinstance(today_attr, list) and len(today_attr) == 24:
                self._today_consumption = [float(x) for x in today_attr]
            else:
                self._today_consumption = [0.0] * 24

            # Restore tracker helpers
            self._last_total_value = last_state.attributes.get("last_total_value")
            self._last_hour = last_state.attributes.get("last_hour", dt_util.now().hour)
            self._last_day = last_state.attributes.get("last_day", dt_util.now().day)
        else:
            self._state = 0.0
            self._today_consumption = [0.0] * 24
            self._last_hour = dt_util.now().hour
            self._last_day = dt_util.now().day

        # Initial fetch of averages from statistics
        await self.async_update_averages()

        # Listener for cumulative sensor changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._target_sensor_id], self._async_sensor_state_listener
            )
        )

        # Hourly cron trigger (minute=0, second=0) for transitions and daily resets
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

    async def async_update_averages(self) -> None:
        """Fetch average hourly consumption for each weekday from DB statistics."""
        from .statistics import async_get_average_hourly_consumption

        ems_log(self.hass, _LOGGER, logging.DEBUG, "Updating EMS weekday average statistics profiles")
        averages = await async_get_average_hourly_consumption(
            self.hass, self._target_sensor_id, self._statistics_days
        )

        if averages:
            for weekday in range(7):
                day_avg = []
                for hour in range(24):
                    val = averages.get(weekday, {}).get(hour, 0.0)
                    if val <= 0.0:
                        val = self._fallback_consumption
                    day_avg.append(round(val, 4))
                self._averages[weekday] = day_avg
        else:
            ems_log(self.hass, _LOGGER, logging.DEBUG, "No statistics database records available, using fallback profile")
            for weekday in range(7):
                self._averages[weekday] = [self._fallback_consumption] * 24

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Handle transitioning to a new hour and resetting daily parameters at midnight."""
        now = dt_util.now()

        # Midnight reset check
        if now.day != self._last_day:
            ems_log(self.hass, _LOGGER, logging.INFO, "EMS load consumption midnight reset triggered")
            self._today_consumption = [0.0] * 24
            self._last_day = now.day
            self._state = 0.0
            # Update averages at the start of a new day
            await self.async_update_averages()

        self._last_hour = now.hour
        self.async_write_ha_state()

    async def _async_sensor_state_listener(self, event) -> None:
        """Track state updates from the target cumulative sensor and calculate hourly deltas."""
        new_state = event.data.get("new_state")
        if new_state is None:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Sensor state for {self._target_sensor_id} is None!")
            return

        if new_state.state in (None, "unknown", "unavailable"):
            ems_log(
                self.hass,
                _LOGGER,
                logging.ERROR,
                f"Sensor state for {self._target_sensor_id} is invalid: {new_state.state}"
            )
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError) as err:
            ems_log(
                self.hass,
                _LOGGER,
                logging.ERROR,
                f"Could not convert sensor state '{new_state.state}' to float for {self._target_sensor_id}: {err}"
            )
            return

        # Successfully retrieved value - log as INFO
        ems_log(
            self.hass,
            _LOGGER,
            logging.INFO,
            f"Successfully retrieved value from {self._target_sensor_id}: {new_value} kWh"
        )

        now = dt_util.now()

        # Double check midnight transition in case cron lagged
        if now.day != self._last_day:
            self._today_consumption = [0.0] * 24
            self._last_day = now.day
            self._last_hour = now.hour
            await self.async_update_averages()

        if self._last_total_value is None:
            # First sensor update since boot - initialize base value
            self._last_total_value = new_value
            self._last_hour = now.hour
            self.async_write_ha_state()
            return

        delta = new_value - self._last_total_value
        if delta < 0:
            # Handle source sensor resets
            self._last_total_value = new_value
            self.async_write_ha_state()
            return

        current_hour = now.hour
        # Update hourly slot and total today value
        self._today_consumption[current_hour] = round(self._today_consumption[current_hour] + delta, 4)
        self._last_total_value = new_value
        self._last_hour = current_hour
        self._state = round(sum(self._today_consumption), 4)

        self.async_write_ha_state()


class EmsPvForecastTodaySensor(SensorEntity):
    """EMS sensor that tracks today's corrected PV forecast."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        device_name: str,
        source_forecast_id: str,
        actual_generation_id: str | None,
    ) -> None:
        """Initialize the forecast sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._source_forecast_id = source_forecast_id
        self._actual_generation_id = actual_generation_id

        self._attr_name = "PV Forecast Today"
        self._attr_unique_id = f"{entry_id}_pv_forecast_today"
        self.entity_id = "sensor.pv_forecast_today"

        # Internal state
        self._state: float = 0.0
        self._baselines: list[float] = [0.0] * 24
        self._forecasts: list[float] = [0.0] * 24
        self._factor: float = 1.0
        self._is_fallback: bool = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> float:
        """Return today's total forecast energy."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "hourly_forecast": self._forecasts,
            "baseline": self._baselines,
            "factor_today": self._factor,
            "source_sensor": self._source_forecast_id,
            "actual_sensor": self._actual_generation_id,
            "forecast_type": "fallback" if self._is_fallback else "forecast",
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()

        # Update initial forecast
        self._update_forecast()

        # Track forecast source changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_forecast_id], self._async_update_listener
            )
        )

        # Track actual generation changes if configured
        if self._actual_generation_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._actual_generation_id], self._async_update_listener
                )
            )

        # Hourly trigger to update elapsed hours
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

    def _update_forecast(self) -> None:
        """Calculate probabilistic baseline and apply Layer 2 corrective factor."""
        state_obj = self.hass.states.get(self._source_forecast_id)
        if not state_obj:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Source PV forecast sensor {self._source_forecast_id} not found!")
            return

        self._baselines, self._is_fallback = parse_solcast_forecast(self.hass, state_obj)

        # Get actual today generation
        actual_today = 0.0
        if self._actual_generation_id:
            gen_state = self.hass.states.get(self._actual_generation_id)
            if gen_state and gen_state.state not in (None, "unknown", "unavailable"):
                try:
                    actual_today = float(gen_state.state)
                except (ValueError, TypeError):
                    pass

        # Calculate baseline for elapsed hours
        now = dt_util.now()
        current_hour = now.hour

        # Sum baseline from hour 0 to current_hour - 1
        baseline_elapsed = sum(self._baselines[0:current_hour])

        # Calculate Layer 2 corrective factor (safeguard against division by zero)
        if baseline_elapsed > 0.0:
            self._factor = max(0.3, min(actual_today / baseline_elapsed, 1.5))
        else:
            self._factor = 1.0

        # Apply factor to remaining hours of today
        new_forecasts = []
        for h in range(24):
            if h >= current_hour:
                new_forecasts.append(round(self._baselines[h] * self._factor, 4))
            else:
                new_forecasts.append(self._baselines[h])

        self._forecasts = new_forecasts
        self._state = round(sum(self._forecasts), 4)

        ems_log(
            self.hass,
            _LOGGER,
            logging.INFO,
            f"Updated Today's PV Forecast: {self._state} kWh (Factor: {self._factor:.3f}, Actual Today: {actual_today} kWh)"
        )

    async def _async_update_listener(self, event) -> None:
        """Handle state change event from source or actual generation sensors."""
        self._update_forecast()
        self.async_write_ha_state()

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Recalculate forecast on hourly transitions."""
        self._update_forecast()
        self.async_write_ha_state()


class EmsPvForecastTomorrowSensor(SensorEntity):
    """EMS sensor that tracks tomorrow's PV forecast."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        device_name: str,
        source_forecast_id: str,
    ) -> None:
        """Initialize the forecast sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._source_forecast_id = source_forecast_id

        self._attr_name = "PV Forecast Tomorrow"
        self._attr_unique_id = f"{entry_id}_pv_forecast_tomorrow"
        self.entity_id = "sensor.pv_forecast_tomorrow"

        # Internal state
        self._state: float = 0.0
        self._baselines: list[float] = [0.0] * 24
        self._is_fallback: bool = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> float:
        """Return tomorrow's total forecast energy."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "hourly_forecast": self._baselines,  # Same as baseline for tomorrow
            "baseline": self._baselines,
            "source_sensor": self._source_forecast_id,
            "forecast_type": "fallback" if self._is_fallback else "forecast",
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()

        self._update_forecast()

        # Track forecast source changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_forecast_id], self._async_update_listener
            )
        )

        # Hourly trigger to keep updated if needed
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

    def _update_forecast(self) -> None:
        """Calculate probabilistic baseline for tomorrow."""
        state_obj = self.hass.states.get(self._source_forecast_id)
        if not state_obj:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Source PV forecast sensor {self._source_forecast_id} not found!")
            return

        self._baselines, self._is_fallback = parse_solcast_forecast(self.hass, state_obj)
        self._state = round(sum(self._baselines), 4)

        ems_log(
            self.hass,
            _LOGGER,
            logging.INFO,
            f"Updated Tomorrow's PV Forecast: {self._state} kWh"
        )

    async def _async_update_listener(self, event) -> None:
        """Handle state change event from source sensor."""
        self._update_forecast()
        self.async_write_ha_state()

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Recalculate forecast on hourly transitions."""
        self._update_forecast()
        self.async_write_ha_state()


class EmsDpSensor(SensorEntity):
    """EMS Dynamic Programming Strategy Sensor."""

    _attr_has_entity_name = True
    SOC_HYSTERESIS = SOC_HYSTERESIS

    def __init__(self, entry_id: str, device_name: str, entry: ConfigEntry) -> None:
        """Initialize the DP strategy sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry
        self._attr_name = "DP Strategy"
        self._attr_unique_id = f"{entry_id}_dp_strategy"
        self.entity_id = "sensor.dp"

        # State and attributes
        self._state: str = "idle"
        self._charge_hours: list[dict] = []
        self._discharge_hours: list[dict] = []
        self._pv_charge_hours: list[dict] = []
        self._self_consume_hours: list[dict] = []
        self._paid_import_hours: list[dict] = []
        self._solar_export_hours: list[dict] = []
        self._schedule: list[dict] = []
        self._stats: dict = {}
        self._error_msg: str | None = None

        # Throttling/hysteresis helpers
        self._last_calc_time: datetime | None = None
        self._last_calc_soc: float | None = None
        self._last_calc_pv_today: float | None = None
        self._last_calc_pv_tomorrow: float | None = None
        self._calc_duration: float | None = None
        self._reactive_debounce_time: datetime | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> str:
        """Return recommended action for current hour."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        return {
            "charge_hours": self._charge_hours,
            "discharge_hours": self._discharge_hours,
            "pv_charge_hours": self._pv_charge_hours,
            "self_consume_hours": self._self_consume_hours,
            "paid_import_hours": self._paid_import_hours,
            "solar_export_hours": self._solar_export_hours,
            "schedule": self._schedule,
            "stats": self._stats,
            "error": self._error_msg,
            "last_calculation": self._last_calc_time.isoformat() if self._last_calc_time else None,
            "calculation_duration": self._calc_duration,
            "overrides": storage.get_overrides(),
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()

        # Initial calculation
        await self.async_update_strategy(force=True)

        # Recalculate on every hour transition
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

        config = self._entry.data
        options = self._entry.options
        price_buy_sensor_id = options.get(CONF_PRICE_BUY_SENSOR, config.get(CONF_PRICE_BUY_SENSOR))
        price_sell_sensor_id = options.get(CONF_PRICE_SELL_SENSOR, config.get(CONF_PRICE_SELL_SENSOR))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))

        # Recalculate on SOC changes with throttling
        if bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_soc_entity_id], self._async_soc_listener
                )
            )

        # Listen for tariff and forecast changes
        generic_listeners = []
        if price_buy_sensor_id:
            generic_listeners.append(price_buy_sensor_id)
        if price_sell_sensor_id:
            generic_listeners.append(price_sell_sensor_id)
        generic_listeners.extend([
            "sensor.pv_forecast_today",
            "sensor.pv_forecast_tomorrow"
        ])

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, generic_listeners, self._async_generic_listener
            )
        )

        # Listen for manual override updates
        self.async_on_remove(
            self.hass.bus.async_listen("ems_schedule_updated", self._async_force_update)
        )

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Handle hourly recalculation."""
        ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: hourly recalculation triggered")
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        storage.cleanup_old_dates(dt_util.now().strftime("%Y-%m-%d"))
        await self.async_update_strategy(force=True)

    async def _async_force_update(self, event) -> None:
        """Force recalculation when overrides change."""
        ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP trigger: manual overrides changed")
        await self.async_update_strategy(force=True)

    async def _async_generic_listener(self, event) -> None:
        """Handle changes in generic sensors immediately."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state:
            # Check if it's a PV forecast update and compare with 5% threshold
            if entity_id in ("sensor.pv_forecast_today", "sensor.pv_forecast_tomorrow"):
                try:
                    new_val = float(new_state.state)
                except (ValueError, TypeError):
                    new_val = 0.0
                
                last_val = (
                    self._last_calc_pv_today 
                    if entity_id == "sensor.pv_forecast_today" 
                    else self._last_calc_pv_tomorrow
                )
                
                if last_val is not None:
                    if last_val == 0.0 and new_val == 0.0:
                        change_pct = 0.0
                    elif last_val == 0.0:
                        change_pct = 1.0
                    else:
                        change_pct = abs(new_val - last_val) / last_val
                    
                    if change_pct < 0.05:
                        ems_log(
                            self.hass,
                            _LOGGER,
                            logging.DEBUG,
                            f"EMS DP: PV forecast update from {entity_id} ignored (change {change_pct * 100:.1f}% < 5%)"
                        )
                        return

            was_invalid = not old_state or old_state.state in (None, "unknown", "unavailable")
            is_valid = new_state.state not in (None, "unknown", "unavailable")
            
            # Check if current plan has only 0.0 prices but now we have parsed prices
            has_no_prices_in_plan = not any(slot.get("buy_price", 0.0) for slot in self._schedule)
            buy_prices_today = self.hass.data.get(DOMAIN, {}).get("price_buy_today", [])
            has_prices_now = any(buy_prices_today)
            
            force = (was_invalid and is_valid) or (has_no_prices_in_plan and has_prices_now)
            
            ems_log(self.hass, _LOGGER, logging.DEBUG, f"EMS DP trigger: update from {entity_id} (force={force})")
            await self.async_update_strategy(force=force)

    async def _async_soc_listener(self, event) -> None:
        """Handle SOC updates with 2% change or 10 min throttle."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            soc = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.now()
        if (
            self._last_calc_time is None
            or self._last_calc_soc is None
            or abs(soc - self._last_calc_soc) >= 2.0
            or (now - self._last_calc_time).total_seconds() > 600
        ):
            ems_log(
                self.hass,
                _LOGGER,
                logging.DEBUG,
                f"EMS DP trigger: SOC changed to {soc}% (last calculated: {self._last_calc_soc}%)"
            )
            await self.async_update_strategy()

    async def async_update_strategy(self, force: bool = False) -> None:
        """Gather states and call DP execution inside executor pool."""
        now = dt_util.now()

        # Debounce/cooldown check
        if not force and self._reactive_debounce_time is not None:
            cooldown_rem = (now - self._reactive_debounce_time).total_seconds()
            if cooldown_rem < 60:
                ems_log(
                    self.hass,
                    _LOGGER,
                    logging.DEBUG,
                    f"EMS DP: update debounced (cooldown: {60 - cooldown_rem:.1f}s remaining)"
                )
                return

        self._reactive_debounce_time = now

        start_time = time.perf_counter()

        try:
            config = self._entry.data
            options = self._entry.options
            storage = self.hass.data[DOMAIN][self._entry_id]["storage"]

            # 1. Validate mandatory configuration keys are present
            required_keys = [
                CONF_TOTAL_LOAD_CONSUMPTION,
                CONF_PV_FORECAST_TODAY,
                CONF_PV_FORECAST_TOMORROW,
                CONF_PRICE_BUY_SENSOR,
                CONF_PRICE_SELL_SENSOR,
                CONF_BAT_CAPACITY_ENTITY,
                CONF_BAT_SOC_ENTITY,
            ]
            from .utils import ems_log
            for key in required_keys:
                entity_id = options.get(key, config.get(key))
                if not entity_id:
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Required configuration parameter '{key}' is missing! Please configure it in integration settings."
                    )
                    self._state = "unavailable"
                    self._error_msg = f"Missing parameter '{key}'"
                    self.async_write_ha_state()
                    return

            total_load_consumption_id = options.get(CONF_TOTAL_LOAD_CONSUMPTION, config.get(CONF_TOTAL_LOAD_CONSUMPTION))
            bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
            bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
            bat_voltage_entity_id = options.get(CONF_BAT_VOLTAGE, config.get(CONF_BAT_VOLTAGE))

            # 2. Check states of required entities
            for entity_id in [
                total_load_consumption_id,
                bat_capacity_entity_id,
                bat_soc_entity_id,
            ]:
                state_obj = self.hass.states.get(entity_id)
                if not state_obj or state_obj.state in (None, "unknown", "unavailable"):
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Required sensor '{entity_id}' is in state '{state_obj.state if state_obj else 'None'}'. Skipping strategy update."
                    )
                    self._state = "unavailable"
                    self._error_msg = f"Sensor '{entity_id}' is unavailable"
                    self.async_write_ha_state()
                    return

            fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))
            min_sell_price = storage.min_sell_price
            bat_max_power = options.get(CONF_BAT_MAX_POWER, config.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER))
            min_bat_soc = storage.min_bat_soc

            # Parse capacity
            capacity = 5.12
            cap_state = self.hass.states.get(bat_capacity_entity_id)
            if cap_state and cap_state.state not in (None, "unknown", "unavailable"):
                try:
                    capacity = float(cap_state.state)
                    unit = cap_state.attributes.get("unit_of_measurement")
                    if unit == "Wh" or capacity > 100.0:
                        capacity = capacity / 1000.0
                except (ValueError, TypeError):
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Battery capacity sensor '{bat_capacity_entity_id}' has non-numeric state '{cap_state.state}'. Skipping strategy update."
                    )
                    self._state = "unavailable"
                    self._error_msg = "Invalid capacity value"
                    self.async_write_ha_state()
                    return

            # Parse current SOC
            soc = 50.0
            soc_state = self.hass.states.get(bat_soc_entity_id)
            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                try:
                    val = float(soc_state.state)
                    if 0.0 <= val <= 100.0:
                        soc = val
                    else:
                        ems_log(
                            self.hass,
                            _LOGGER,
                            logging.ERROR,
                            f"Battery SOC value {val}% from '{bat_soc_entity_id}' is outside the valid range [0, 100]. Please verify that 'Battery State of Charge (SOC)' is configured with the correct sensor in integration settings."
                        )
                        self._state = "unavailable"
                        self._error_msg = f"Invalid SOC value {val}%"
                        self.async_write_ha_state()
                        return
                except (ValueError, TypeError):
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Battery SOC sensor '{bat_soc_entity_id}' has non-numeric state '{soc_state.state}'. Skipping strategy update."
                    )
                    self._state = "unavailable"
                    self._error_msg = "Invalid SOC value type"
                    self.async_write_ha_state()
                    return

            # Apply SOC hysteresis
            effective_soc = soc
            if (
                self._state not in ("discharge", "self_consume")
                and self._last_calc_soc is not None
                and soc < min_bat_soc + self.SOC_HYSTERESIS
            ):
                effective_soc = min(soc, min_bat_soc)

            cycle_cost = self.hass.data.get(DOMAIN, {}).get("bat_degradation_per_kwh", 0.0)

            buy_prices_today = self.hass.data.get(DOMAIN, {}).get("price_buy_today", [0.0] * 24)
            buy_prices_tomorrow = self.hass.data.get(DOMAIN, {}).get("price_buy_tomorrow", [0.0] * 24)
            sell_prices_today = self.hass.data.get(DOMAIN, {}).get("price_sell_today", [0.0] * 24)
            sell_prices_tomorrow = self.hass.data.get(DOMAIN, {}).get("price_sell_tomorrow", [0.0] * 24)

            pv_today = [0.0] * 24
            pv_today_state = self.hass.states.get("sensor.pv_forecast_today")
            if pv_today_state:
                pv_today = pv_today_state.attributes.get("hourly_forecast", [0.0] * 24)

            pv_tomorrow = [0.0] * 24
            pv_tomorrow_state = self.hass.states.get("sensor.pv_forecast_tomorrow")
            if pv_tomorrow_state:
                pv_tomorrow = pv_tomorrow_state.attributes.get("hourly_forecast", [0.0] * 24)

            consumption_today = [fallback_consumption] * 24
            consumption_tomorrow = [fallback_consumption] * 24
            load_state = self.hass.states.get("sensor.load_consumption")
            if load_state:
                consumption_today = load_state.attributes.get("average_today", [fallback_consumption] * 24)
                now = dt_util.now()
                tomorrow_weekday = (now + timedelta(days=1)).weekday()
                day_keys = [
                    "average_monday", "average_tuesday", "average_wednesday",
                    "average_thursday", "average_friday", "average_saturday",
                    "average_sunday",
                ]
                tomorrow_key = day_keys[tomorrow_weekday]
                consumption_tomorrow = load_state.attributes.get(tomorrow_key, [fallback_consumption] * 24)

            # Sanitize lists with safe_float_list
            buy_prices_today = safe_float_list(buy_prices_today, 0.0)
            buy_prices_tomorrow = safe_float_list(buy_prices_tomorrow, 0.0)
            sell_prices_today = safe_float_list(sell_prices_today, 0.0)
            sell_prices_tomorrow = safe_float_list(sell_prices_tomorrow, 0.0)
            pv_today = safe_float_list(pv_today, 0.0)
            pv_tomorrow = safe_float_list(pv_tomorrow, 0.0)
            consumption_today = safe_float_list(consumption_today, fallback_consumption)
            consumption_tomorrow = safe_float_list(consumption_tomorrow, fallback_consumption)

            storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
            overrides = storage.get_overrides()

            result = await self.hass.async_add_executor_job(
                self._calculate_strategy_sync,
                effective_soc,
                capacity,
                min_bat_soc,
                bat_max_power,
                min_sell_price,
                storage.min_energy_to_discharge,
                cycle_cost,
                buy_prices_today,
                buy_prices_tomorrow,
                sell_prices_today,
                sell_prices_tomorrow,
                pv_today,
                pv_tomorrow,
                consumption_today,
                consumption_tomorrow,
                fallback_consumption,
                overrides,
            )

            self._state = result.get("current_action", "idle")
            self._charge_hours = result.get("charge_hours", [])
            self._discharge_hours = result.get("discharge_hours", [])
            self._pv_charge_hours = result.get("pv_charge_hours", [])
            self._self_consume_hours = result.get("self_consume_hours", [])
            self._paid_import_hours = result.get("paid_import_hours", [])
            self._solar_export_hours = result.get("solar_export_hours", [])
            self._schedule = result.get("schedule", [])
            self._stats = result.get("stats", {})
            self._error_msg = result.get("error")

            self._last_calc_time = dt_util.now()
            self._last_calc_soc = soc

            # Update last calculated PV forecast values
            pv_today_entity = self.hass.states.get("sensor.pv_forecast_today")
            if pv_today_entity:
                try:
                    self._last_calc_pv_today = float(pv_today_entity.state)
                except (ValueError, TypeError):
                    self._last_calc_pv_today = 0.0

            pv_tomorrow_entity = self.hass.states.get("sensor.pv_forecast_tomorrow")
            if pv_tomorrow_entity:
                try:
                    self._last_calc_pv_tomorrow = float(pv_tomorrow_entity.state)
                except (ValueError, TypeError):
                    self._last_calc_pv_tomorrow = 0.0

            self._calc_duration = round(time.perf_counter() - start_time, 3)

            self.async_write_ha_state()
        except Exception as err:
            self._calc_duration = None
            ems_log(
                self.hass,
                _LOGGER,
                logging.ERROR,
                f"Error in async_update_strategy: {err}",
                exc_info=True
            )

    def _calculate_strategy_sync(
        self,
        soc: float,
        capacity: float,
        min_bat_soc: float,
        bat_max_power: float,
        min_sell_price: float,
        min_energy_to_discharge: float,
        cycle_cost: float,
        buy_prices_today: list[float],
        buy_prices_tomorrow: list[float],
        sell_prices_today: list[float],
        sell_prices_tomorrow: list[float],
        pv_today: list[float],
        pv_tomorrow: list[float],
        consumption_today: list[float],
        consumption_tomorrow: list[float],
        fallback_consumption: float,
        overrides: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """Build grid of slots and call DP core helper."""
        from .dp_engine import run_unified_dp, DPConfig

        now = dt_util.now()
        current_hour = now.hour
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        slots = []
        # Today
        for h in range(current_hour, 24):
            override = overrides.get(today_str, {}).get(str(h))
            slots.append({
                "date": today_str,
                "hour": h,
                "buy_price": buy_prices_today[h] if h < len(buy_prices_today) else 0.0,
                "sell_price": sell_prices_today[h] if h < len(sell_prices_today) else 0.0,
                "pv_kwh": pv_today[h] if h < len(pv_today) else 0.0,
                "consumption_kwh": consumption_today[h] if h < len(consumption_today) else fallback_consumption,
                "override": override,
            })
        # Tomorrow
        has_tomorrow_prices = any(buy_prices_tomorrow) or any(sell_prices_tomorrow)
        if has_tomorrow_prices:
            for h in range(24):
                override = overrides.get(tomorrow_str, {}).get(str(h))
                slots.append({
                    "date": tomorrow_str,
                    "hour": h,
                    "buy_price": buy_prices_tomorrow[h] if h < len(buy_prices_tomorrow) else 0.0,
                    "sell_price": sell_prices_tomorrow[h] if h < len(sell_prices_tomorrow) else 0.0,
                    "pv_kwh": pv_tomorrow[h] if h < len(pv_tomorrow) else 0.0,
                    "consumption_kwh": consumption_tomorrow[h] if h < len(consumption_tomorrow) else fallback_consumption,
                    "override": override,
                })

        usable_capacity = capacity * (1 - min_bat_soc / 100)
        current_usable = capacity * (soc - min_bat_soc) / 100
        current_usable = max(0.0, min(current_usable, usable_capacity))

        # Reserve calculation (night hours average demand)
        night_hours = [23, 0, 1, 2, 3, 4, 5, 6, 7]
        profile = consumption_tomorrow if has_tomorrow_prices else consumption_today
        reserve = sum(profile[h] for h in night_hours if h < len(profile))
        min_end_usable = min(reserve, usable_capacity)

        horizon_buy = [slot["buy_price"] for slot in slots]
        global_min_buy = min(horizon_buy) if horizon_buy else 0.0
        terminal_value = max(min_sell_price, global_min_buy + cycle_cost)

        dp_config = DPConfig(
            min_sell_price=min_sell_price,
            battery_max_discharge_power=bat_max_power / 1000.0,
            battery_max_charge_power=bat_max_power / 1000.0,
            battery_min_soc=int(min_bat_soc),
            battery_capacity=capacity,
            min_energy_to_discharge=min_energy_to_discharge,
            disable_discharge=False,
        )

        try:
            (
                chg_h,
                dis_h,
                pvc_h,
                sc_h,
                pim_h,
                stats,
            ) = run_unified_dp(
                slots=slots,
                current_usable=current_usable,
                usable_capacity=usable_capacity,
                cycle_cost=cycle_cost,
                terminal_value_per_kwh=terminal_value,
                min_end_usable=min_end_usable,
                config=dp_config,
            )

            # Check if total planned discharge is less than the minimum configured limit
            if (
                dp_config.min_energy_to_discharge > 0.0
                and 0.0 < stats.get("planned_battery_discharge_kwh", 0.0) < dp_config.min_energy_to_discharge
            ):
                _LOGGER.info(
                    "Planned battery discharge (%.2f kWh) is less than minimum limit (%.2f kWh). Re-running DP with discharge disabled.",
                    stats.get("planned_battery_discharge_kwh", 0.0),
                    dp_config.min_energy_to_discharge,
                )
                dp_config.disable_discharge = True
                (
                    chg_h,
                    dis_h,
                    pvc_h,
                    sc_h,
                    pim_h,
                    stats,
                ) = run_unified_dp(
                    slots=slots,
                    current_usable=current_usable,
                    usable_capacity=usable_capacity,
                    cycle_cost=cycle_cost,
                    terminal_value_per_kwh=terminal_value,
                    min_end_usable=min_end_usable,
                    config=dp_config,
                )
        except Exception as ex:
            return {
                "current_action": "idle",
                "error": f"DP calculation error: {str(ex)}",
            }

        current_action = "idle"
        solar_export_hours = []
        schedule = []

        chg_keys = {(h["date"], h["hour"]): h for h in chg_h}
        dis_keys = {(h["date"], h["hour"]): h for h in dis_h}
        pvc_keys = {(h["date"], h["hour"]): h for h in pvc_h}
        sc_keys = {(h["date"], h["hour"]): h for h in sc_h}
        pim_keys = {(h["date"], h["hour"]): h for h in pim_h}

        for idx, slot in enumerate(slots):
            key = (slot["date"], slot["hour"])
            action = "idle"
            energy = 0.0

            if key in chg_keys:
                action = "grid_charge"
                energy = chg_keys[key].get("planned_energy_kwh", 0.0)
            elif key in dis_keys:
                action = "discharge"
                energy = dis_keys[key].get("planned_energy_kwh", 0.0)
            elif key in pvc_keys:
                action = "pv_charge"
                energy = pvc_keys[key].get("charge_kwh", 0.0)
            elif key in sc_keys:
                action = "self_consume"
                energy = sc_keys[key].get("planned_energy_kwh", 0.0)
            elif key in pim_keys:
                action = "paid_import"
                energy = pim_keys[key].get("planned_grid_import_kwh", 0.0)
            else:
                if slot["pv_kwh"] > 0.1:
                    action = "solar_export"
                    solar_export_hours.append({
                        "date": slot["date"],
                        "hour": slot["hour"],
                        "pv_kwh": slot["pv_kwh"],
                    })

            # Calculate cheap_ahead for the slot (next 6 hours, ONLY negative price)
            cheap_ahead = False
            if action != "self_consume":
                horizon_end = min(idx + 7, len(slots))
                for f_idx in range(idx + 1, horizon_end):
                    future_p_buy = slots[f_idx]["buy_price"]
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            physical_mode, mapping_reason = map_dp_to_physical(
                action=action,
                sell_price=slot["sell_price"],
                pv_kwh=slot["pv_kwh"],
                min_sell_price=min_sell_price,
                cheap_ahead=cheap_ahead,
            )

            if idx == 0:
                current_action = action

            schedule.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "buy_price": slot["buy_price"],
                "sell_price": slot["sell_price"],
                "pv_kwh": slot["pv_kwh"],
                "consumption_kwh": slot["consumption_kwh"],
                "action": action,
                "physical_mode": physical_mode,
                "mapping_reason": mapping_reason,
                "energy_kwh": round(energy, 2),
            })

        return {
            "current_action": current_action,
            "charge_hours": chg_h,
            "discharge_hours": dis_h,
            "pv_charge_hours": pvc_h,
            "self_consume_hours": sc_h,
            "paid_import_hours": pim_h,
            "solar_export_hours": solar_export_hours,
            "schedule": schedule,
            "stats": stats,
            "error": None,
        }


class EmsSchedulerSensor(SensorEntity):
    """EMS Scheduler State and Overrides Sensor."""

    _attr_has_entity_name = True

    def __init__(self, entry_id: str, device_name: str, entry: ConfigEntry) -> None:
        """Initialize the scheduler sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry
        self._attr_name = "Scheduler"
        self._attr_unique_id = f"{entry_id}_scheduler"
        self.entity_id = "sensor.scheduler"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the scheduler (current active mode)."""
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        overrides = storage.get_overrides()

        # Calculate active override for the current hour
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        active_override = overrides.get(today_str, {}).get(str(current_hour))

        dp_state = self.hass.states.get("sensor.dp")

        if active_override is not None:
            schedule = dp_state.attributes.get("schedule", []) if dp_state is not None else []
            current_slot = schedule[0] if schedule else {}
            sell_price = current_slot.get("sell_price", 0.0)
            pv_kwh = current_slot.get("pv_kwh", 0.0)

            cheap_ahead = False
            if active_override != "self_consume" and schedule:
                horizon_end = min(7, len(schedule))
                for f_idx in range(1, horizon_end):
                    future_p_buy = schedule[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            physical_mode, _ = map_dp_to_physical(
                action=active_override,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=storage.min_sell_price,
                cheap_ahead=cheap_ahead,
            )
            return physical_mode

        if dp_state is None or dp_state.state in ("unknown", "unavailable"):
            return None

        schedule = dp_state.attributes.get("schedule", [])
        if not schedule:
            return None

        return schedule[0].get("physical_mode", dp_state.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        dp_state = self.hass.states.get("sensor.dp")

        schedule = []
        last_dp_call = None
        if dp_state is not None:
            schedule = dp_state.attributes.get("schedule", [])
            last_dp_call = dp_state.attributes.get("last_calculation")

        overrides = storage.get_overrides()

        # Calculate active override for the current hour
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        active_override = overrides.get(today_str, {}).get(str(current_hour))

        # Retrieve configuration details for SOC estimation
        config = self._entry.data
        options = self._entry.options
        from .const import (
            CONF_BAT_CAPACITY_ENTITY,
            CONF_BAT_SOC_ENTITY,
            CONF_MIN_BAT_SOC,
            DEFAULT_MIN_BAT_SOC,
        )

        bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        bat_voltage_entity_id = options.get(CONF_BAT_VOLTAGE, config.get(CONF_BAT_VOLTAGE))
        min_bat_soc = storage.min_bat_soc

        # Parse capacity
        capacity = 5.12
        if bat_capacity_entity_id:
            cap_state = self.hass.states.get(bat_capacity_entity_id)
            if cap_state and cap_state.state not in (None, "unknown", "unavailable"):
                try:
                    capacity = float(cap_state.state)
                    unit = cap_state.attributes.get("unit_of_measurement")
                    if unit == "Wh" or capacity > 100.0:
                        capacity = capacity / 1000.0
                except (ValueError, TypeError):
                    pass

        # Parse SOC
        soc = 50.0
        if bat_soc_entity_id:
            soc_state = self.hass.states.get(bat_soc_entity_id)
            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                try:
                    val = float(soc_state.state)
                    if 0.0 <= val <= 100.0:
                        soc = val
                    else:
                        from .utils import ems_log
                        ems_log(
                            self.hass,
                            _LOGGER,
                            logging.ERROR,
                            f"Battery SOC value {val}% from '{bat_soc_entity_id}' is outside the valid range [0, 100]. Please verify that 'Battery State of Charge (SOC)' is configured with the correct sensor in integration settings."
                        )
                except (ValueError, TypeError):
                    pass

        # Parse voltage
        voltage = 51.2
        if bat_voltage_entity_id and bat_voltage_entity_id != bat_capacity_entity_id:
            volt_state = self.hass.states.get(bat_voltage_entity_id)
            if volt_state and volt_state.state not in (None, "unknown", "unavailable"):
                try:
                    voltage = float(volt_state.state)
                except (ValueError, TypeError):
                    pass
        safe_voltage = voltage if voltage > 0.0 else 51.2

        # Simulate SOC, power and current progression over the plan
        usable_capacity = capacity * (1 - min_bat_soc / 100)
        current_usable = capacity * (soc - min_bat_soc) / 100
        current_usable = max(0.0, min(current_usable, usable_capacity))

        usable_energy = current_usable
        safe_capacity = capacity if capacity > 0.0 else 5.12

        dispatched_plan = []
        for slot in schedule:
            action = slot.get("action", "idle")
            energy = slot.get("energy_kwh", 0.0)

            power_w = 0.0
            current_a = 0.0

            if action in ("grid_charge", "pv_charge"):
                end_usable = min(usable_capacity, usable_energy + energy)
                power_w = energy * 1000.0
                current_a = power_w / safe_voltage
            elif action in ("discharge", "self_consume"):
                end_usable = max(0.0, usable_energy - energy)
                power_w = energy * 1000.0
                current_a = power_w / safe_voltage
            else:
                end_usable = usable_energy

            # Calculate SOC at the end of the hour
            end_soc = (end_usable / safe_capacity) * 100 + min_bat_soc
            end_soc = max(min_bat_soc, min(100.0, end_soc))

            dispatched_plan.append({
                **slot,
                "soc": round(end_soc, 1),
                "power_w": round(power_w, 1),
                "current_a": round(current_a, 1),
            })
            usable_energy = end_usable

        # Determine raw_mode and mapping_reason for the current state
        raw_mode = None
        mapping_reason = None

        if dp_state is not None and dp_state.state not in (None, "unknown", "unavailable"):
            raw_mode = dp_state.state

        if schedule:
            raw_mode = schedule[0].get("action", raw_mode)
            mapping_reason = schedule[0].get("mapping_reason")

        if active_override is not None:
            raw_mode = active_override
            current_slot = schedule[0] if schedule else {}
            sell_price = current_slot.get("sell_price", 0.0)
            pv_kwh = current_slot.get("pv_kwh", 0.0)

            cheap_ahead = False
            if active_override != "self_consume" and schedule:
                horizon_end = min(7, len(schedule))
                for f_idx in range(1, horizon_end):
                    future_p_buy = schedule[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            _, override_reason = map_dp_to_physical(
                action=active_override,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=storage.min_sell_price,
                cheap_ahead=cheap_ahead,
            )
            mapping_reason = f"override: {active_override} | {override_reason}"

        current_power = 0.0
        current_amps = 0.0
        current_target_soc = soc

        if dispatched_plan:
            current_slot = dispatched_plan[0]
            current_power = current_slot.get("power_w", 0.0)
            current_amps = current_slot.get("current_a", 0.0)
            current_target_soc = current_slot.get("soc", soc)

        return {
            "current_plan": dispatched_plan,
            "last_dp_call": last_dp_call,
            "last_override_change": storage.last_override_change,
            "overrides": overrides,
            "active_override": active_override,
            "raw_mode": raw_mode,
            "mapping_reason": mapping_reason,
            "battery_soc": soc,
            "power": current_power,
            "target_soc": current_target_soc,
            "amps": current_amps,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()

        # Listen for state changes of sensor.dp to update plan/scheduler state
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, ["sensor.dp"], self._async_dp_changed
            )
        )

        # Listen for manual override updates
        self.async_on_remove(
            self.hass.bus.async_listen("ems_schedule_updated", self._async_override_changed)
        )

    async def _async_dp_changed(self, event) -> None:
        """Handle DP sensor changes."""
        self.async_write_ha_state()

    async def _async_override_changed(self, event) -> None:
        """Handle manual override updates."""
        self.async_write_ha_state()
