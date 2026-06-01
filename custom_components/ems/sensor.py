"""Sensor platform for EMS integration."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
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

from .utils import ems_log

from .const import (
    DOMAIN,
    VERSION,
    CONF_TOTAL_LOAD_CONSUMPTION,
    CONF_TOTAL_GRID_EXPORT,
    CONF_TOTAL_GRID_IMPORT,
    CONF_CURRENT_HOUSE_CONSUMPTION,
    CONF_INVERTER_MODES_LIST,
    CONF_CURRENT_PV_GENERATION,
    CONF_PV_GENERATION_TODAY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    CONF_DEBUG,
    CONF_VACATION_MODE_ENTITY,
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
    CONF_THERMOSTAT_SET_TEMP,
    CONF_ELEC_BOILER_MAX_TEMP,
    CONF_GAS_BOILER_MAX_TEMP,
    DEFAULT_THERMOSTAT_SET_TEMP,
    DEFAULT_ELEC_BOILER_MAX_TEMP,
    DEFAULT_GAS_BOILER_MAX_TEMP,
    STANDBY_LOSSES_PRESETS,
)
from .utils import ems_log, calculate_battery_degradation, parse_price_sensor, map_dp_to_physical, map_override_to_physical
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
    total_grid_export_id = options.get(CONF_TOTAL_GRID_EXPORT, config.get(CONF_TOTAL_GRID_EXPORT))
    total_grid_import_id = options.get(CONF_TOTAL_GRID_IMPORT, config.get(CONF_TOTAL_GRID_IMPORT))
    current_house_consumption_id = options.get(CONF_CURRENT_HOUSE_CONSUMPTION, config.get(CONF_CURRENT_HOUSE_CONSUMPTION))
    inverter_modes_list_id = options.get(CONF_INVERTER_MODES_LIST, config.get(CONF_INVERTER_MODES_LIST))
    statistics_days = options.get(CONF_STATISTICS_DAYS, config.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS))
    fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))

    # Boiler settings (to pass energy sensor)
    elec_boiler_energy_id = options.get("elec_boiler_energy", config.get("elec_boiler_energy"))

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
                elec_boiler_energy_id,
            )
        )

    if pv_today_id:
        entities.append(
            EmsPvForecastTodaySensor(
                entry.entry_id,
                entry.title,
                pv_today_id,
                pv_generation_today_id,
                inverter_modes_list_id,
                bat_soc_entity_id,
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

    if total_grid_export_id and total_grid_import_id and target_sensor_id and price_buy_sensor_id and price_sell_sensor_id:
        entities.append(
            EmsTodayProfitSensor(
                entry_id=entry.entry_id,
                device_name=entry.title,
                load_consumption_sensor_id=target_sensor_id,
                grid_export_sensor_id=total_grid_export_id,
                grid_import_sensor_id=total_grid_import_id,
                price_buy_sensor_id=price_buy_sensor_id,
                price_sell_sensor_id=price_sell_sensor_id,
            )
        )
        entities.append(
            EmsRoiSensor(
                entry_id=entry.entry_id,
                device_name=entry.title,
                entry=entry,
            )
        )

    # Always register calibration sensor
    entities.append(
        EmsBoilerCalibrationSensor(
            entry.entry_id,
            entry.title,
        )
    )
    entities.append(
        EmsDiagnosticSensor(
            entry.entry_id,
            entry.title,
            entry,
        )
    )
    if bat_soc_entity_id and bat_capacity_entity_id:
        entities.append(
            EmsBoilerDpSensor(
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
        boiler_sensor_id: str | None = None,
    ) -> None:
        """Initialize the load consumption sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._target_sensor_id = target_sensor_id
        self._boiler_sensor_id = boiler_sensor_id
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

        self._boiler_today_consumption: list[float] = [0.0] * 24
        self._boiler_last_total_value: float | None = None

        # Weekday averages mapping weekday (0-6) -> 24-element list
        self._averages: dict[int, list[float]] = {}
        self._boiler_averages: dict[int, list[float]] = {}
        for weekday in range(7):
            self._averages[weekday] = [self._fallback_consumption] * 24
            self._boiler_averages[weekday] = [0.0] * 24

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

        if self._boiler_sensor_id:
            attrs.update({
                "boiler_today": self._boiler_today_consumption,
                "boiler_average_today": self._boiler_averages.get(today_weekday, [0.0] * 24),
                "boiler_last_total_value": self._boiler_last_total_value,
            })
            for idx, key in enumerate(day_keys):
                attrs[f"boiler_{key}"] = self._boiler_averages.get(idx, [0.0] * 24)

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
            
            if self._boiler_sensor_id:
                boiler_today_attr = last_state.attributes.get("boiler_today")
                if isinstance(boiler_today_attr, list) and len(boiler_today_attr) == 24:
                    self._boiler_today_consumption = [float(x) for x in boiler_today_attr]
                else:
                    self._boiler_today_consumption = [0.0] * 24
                self._boiler_last_total_value = last_state.attributes.get("boiler_last_total_value")
        else:
            self._state = 0.0
            self._today_consumption = [0.0] * 24
            self._last_hour = dt_util.now().hour
            self._last_day = dt_util.now().day
            self._boiler_today_consumption = [0.0] * 24
            self._boiler_last_total_value = None

        # Initial fetch of averages from statistics
        await self.async_update_averages()

        # Listener for cumulative sensor changes
        listen_entities = [self._target_sensor_id]
        if self._boiler_sensor_id:
            listen_entities.append(self._boiler_sensor_id)
            
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, listen_entities, self._async_sensor_state_listener
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
        
        tasks = [
            async_get_average_hourly_consumption(self.hass, self._target_sensor_id, self._statistics_days)
        ]
        if self._boiler_sensor_id:
            tasks.append(
                async_get_average_hourly_consumption(self.hass, self._boiler_sensor_id, self._statistics_days)
            )
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        averages = results[0] if not isinstance(results[0], Exception) else None
        boiler_averages = results[1] if self._boiler_sensor_id and len(results) > 1 and not isinstance(results[1], Exception) else None

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

        if self._boiler_sensor_id:
            if boiler_averages:
                for weekday in range(7):
                    day_avg = []
                    for hour in range(24):
                        val = boiler_averages.get(weekday, {}).get(hour, 0.0)
                        day_avg.append(round(max(val, 0.0), 4))
                    self._boiler_averages[weekday] = day_avg
            else:
                ems_log(self.hass, _LOGGER, logging.DEBUG, "No boiler statistics available, using zero fallback")
                for weekday in range(7):
                    self._boiler_averages[weekday] = [0.0] * 24

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Handle transitioning to a new hour and resetting daily parameters at midnight."""
        now = dt_util.now()

        # Midnight reset check
        if now.day != self._last_day:
            ems_log(self.hass, _LOGGER, logging.INFO, "EMS load consumption midnight reset triggered")
            self._today_consumption = [0.0] * 24
            self._boiler_today_consumption = [0.0] * 24
            self._last_day = now.day
            self._state = 0.0
            # Update averages at the start of a new day
            await self.async_update_averages()
        self._last_hour = now.hour
        self.async_write_ha_state()

    async def _async_sensor_state_listener(self, event) -> None:
        """Track state updates from the target cumulative sensors and calculate hourly deltas."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state is None:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Sensor state for {entity_id} is None!")
            return

        if new_state.state in (None, "unknown", "unavailable"):
            ems_log(
                self.hass,
                _LOGGER,
                logging.ERROR,
                f"Sensor state for {entity_id} is invalid: {new_state.state}"
            )
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError) as err:
            ems_log(
                self.hass,
                _LOGGER,
                logging.ERROR,
                f"Could not convert sensor state '{new_state.state}' to float for {entity_id}: {err}"
            )
            return

        # Successfully retrieved value - log as INFO
        ems_log(
            self.hass,
            _LOGGER,
            logging.INFO,
            f"Successfully retrieved value from {entity_id}: {new_value} kWh"
        )

        now = dt_util.now()

        # Double check midnight transition in case cron lagged
        if now.day != self._last_day:
            self._today_consumption = [0.0] * 24
            self._boiler_today_consumption = [0.0] * 24
            self._last_day = now.day
            self._last_hour = now.hour
            await self.async_update_averages()

        is_boiler = (entity_id == self._boiler_sensor_id)
        last_val = self._boiler_last_total_value if is_boiler else self._last_total_value

        if last_val is None:
            # First sensor update since boot - initialize base value
            if is_boiler:
                self._boiler_last_total_value = new_value
            else:
                self._last_total_value = new_value
            self._last_hour = now.hour
            self.async_write_ha_state()
            return

        delta = new_value - last_val
        if delta < 0:
            # Handle source sensor resets
            if is_boiler:
                self._boiler_last_total_value = new_value
            else:
                self._last_total_value = new_value
            self.async_write_ha_state()
            return

        current_hour = now.hour
        
        if is_boiler:
            self._boiler_today_consumption[current_hour] = round(self._boiler_today_consumption[current_hour] + delta, 4)
            self._boiler_last_total_value = new_value
        else:
            self._today_consumption[current_hour] = round(self._today_consumption[current_hour] + delta, 4)
            self._last_total_value = new_value
            
            # Update the main state (cumulative consumption for today)
            self._state = round(sum(self._today_consumption), 4)

        self._last_hour = current_hour
        self.async_write_ha_state()


class EmsPvForecastTodaySensor(RestoreSensor, SensorEntity):
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
        inverter_modes_list_id: str | None = None,
        bat_soc_entity_id: str | None = None,
    ) -> None:
        """Initialize the forecast sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._source_forecast_id = source_forecast_id
        self._actual_generation_id = actual_generation_id
        self._inverter_modes_list_id = inverter_modes_list_id
        self._bat_soc_entity_id = bat_soc_entity_id

        self._attr_name = "PV Forecast Today"
        self._attr_unique_id = f"{entry_id}_pv_forecast_today"
        self.entity_id = "sensor.pv_forecast_today"

        # Internal state
        self._state: float = 0.0
        self._baselines: list[float] = [0.0] * 24
        self._forecasts: list[float] = [0.0] * 24
        self._factor: float = 1.0
        self._is_fallback: bool = False
        self._curtail_occurred_today: bool = False
        self._last_day: int = dt_util.now().day
        self._actual_today_hourly: list[float] = [0.0] * 24
        self._last_actual_total: float | None = None

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
            "curtail_occurred_today": self._curtail_occurred_today,
            "inverter_mode_sensor": self._inverter_modes_list_id,
            "battery_soc_sensor": self._bat_soc_entity_id,
            "actual_today_hourly": self._actual_today_hourly,
            "last_actual_total": self._last_actual_total,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore historical states."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            try:
                self._state = float(last_state.state)
            except (ValueError, TypeError):
                self._state = 0.0

            factor_attr = last_state.attributes.get("factor_today")
            if factor_attr is not None:
                try:
                    self._factor = float(factor_attr)
                except (ValueError, TypeError):
                    self._factor = 1.0
            else:
                self._factor = 1.0

            self._curtail_occurred_today = bool(last_state.attributes.get("curtail_occurred_today", False))

            actual_hourly_attr = last_state.attributes.get("actual_today_hourly")
            if isinstance(actual_hourly_attr, list) and len(actual_hourly_attr) == 24:
                self._actual_today_hourly = [float(x) for x in actual_hourly_attr]
            else:
                self._actual_today_hourly = [0.0] * 24

            self._last_actual_total = last_state.attributes.get("last_actual_total")
        else:
            self._state = 0.0
            self._factor = 1.0
            self._curtail_occurred_today = False
            self._actual_today_hourly = [0.0] * 24
            self._last_actual_total = None

        self._last_day = dt_util.now().day

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

        # Track inverter mode changes if configured
        if self._inverter_modes_list_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._inverter_modes_list_id], self._async_update_listener
                )
            )

        # Track battery SOC changes if configured
        if self._bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._bat_soc_entity_id], self._async_update_listener
                )
            )

        # Hourly trigger to update elapsed hours and handle midnight transitions
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

    def _update_forecast(self) -> None:
        """Calculate probabilistic baseline and apply Layer 2 corrective factor with curtailment check."""
        state_obj = self.hass.states.get(self._source_forecast_id)
        if not state_obj:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Source PV forecast sensor {self._source_forecast_id} not found!")
            return

        self._baselines, self._is_fallback = parse_solcast_forecast(self.hass, state_obj)

        now = dt_util.now()
        current_hour = now.hour

        # Midnight transition check
        if now.day != self._last_day:
            self._curtail_occurred_today = False
            self._actual_today_hourly = [0.0] * 24
            self._last_actual_total = None
            self._last_day = now.day

        # Get actual today generation
        actual_today = 0.0
        if self._actual_generation_id:
            gen_state = self.hass.states.get(self._actual_generation_id)
            if gen_state and gen_state.state not in (None, "unknown", "unavailable"):
                try:
                    actual_today = float(gen_state.state)
                except (ValueError, TypeError):
                    pass

        # Check if curtailment is active in the current inverter mode and battery SOC >= calibration_limit_soc
        curtail_active = False
        if self._inverter_modes_list_id:
            mode_state = self.hass.states.get(self._inverter_modes_list_id)
            if mode_state and mode_state.state not in (None, "unknown", "unavailable"):
                try:
                    from .const import INVERTER_MODES
                    mode_config = INVERTER_MODES.get(mode_state.state)
                    if mode_config and mode_config.curtail_pv:
                        soc = 0.0
                        if self._bat_soc_entity_id:
                            soc_state = self.hass.states.get(self._bat_soc_entity_id)
                            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                                try:
                                    soc = float(soc_state.state)
                                except (ValueError, TypeError):
                                    pass
                        # Ограничение генерации действительно только если текущий SOC >= порога калибровки режима
                        if soc >= mode_config.calibration_limit_soc:
                            curtail_active = True
                except Exception as err:
                    ems_log(self.hass, _LOGGER, logging.ERROR, f"Error checking inverter mode curtail_pv: {err}")

        if curtail_active:
            self._curtail_occurred_today = True

        # Only update Layer 2 factor if curtailment is not active and hasn't occurred today
        if not self._curtail_occurred_today:
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
            f"Updated Today's PV Forecast: {self._state} kWh (Factor: {self._factor:.3f}, Curtail: {self._curtail_occurred_today}, Actual Today: {actual_today} kWh)"
        )

    async def _async_update_listener(self, event) -> None:
        """Handle state change event from source, actual generation, mode or SOC sensors."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        # Double check midnight transition in case cron lagged
        now = dt_util.now()
        if now.day != self._last_day:
            self._curtail_occurred_today = False
            self._actual_today_hourly = [0.0] * 24
            self._last_actual_total = None
            self._last_day = now.day

        if entity_id == self._actual_generation_id and new_state is not None:
            if new_state.state not in (None, "unknown", "unavailable"):
                try:
                    new_value = float(new_state.state)
                    current_hour = now.hour

                    if self._last_actual_total is None:
                        # First update - initialize baseline without delta
                        self._last_actual_total = new_value
                    else:
                        delta = new_value - self._last_actual_total
                        if delta < 0:
                            # Handle reset (e.g. at midnight or sensor reload)
                            self._last_actual_total = new_value
                        elif delta <= 15.0:  # Ignore anomalous jumps
                            self._actual_today_hourly[current_hour] = round(
                                self._actual_today_hourly[current_hour] + delta, 4
                            )
                            self._last_actual_total = new_value
                        else:
                            # Spike detected, sync baseline without adding delta
                            self._last_actual_total = new_value
                except (ValueError, TypeError):
                    pass

        self._update_forecast()
        self.async_write_ha_state()

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Recalculate forecast on hourly transitions and handle midnight transitions."""
        now = dt_util.now()
        if now.day != self._last_day:
            self._curtail_occurred_today = False
            self._actual_today_hourly = [0.0] * 24
            self._last_actual_total = None
            self._last_day = now.day
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
    _attr_should_poll = False
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
        self._vacation_mode: bool = False
        self._boiler_average_budget_today: float = 0.0
        self._boiler_average_profile_today: list[float] = [0.0] * 24
        self._boiler_average_budget_tomorrow: float = 0.0
        self._boiler_average_profile_tomorrow: list[float] = [0.0] * 24
        self._boiler_expected_consumption_today: float = 0.0
        self._boiler_expected_consumption_tomorrow: float = 0.0
        self._curtailed_pv_today: float = 0.0
        self._curtailed_pv_tomorrow: float = 0.0

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
            "vacation_mode": self._vacation_mode,
            "boiler_average_budget_today": self._boiler_average_budget_today,
            "boiler_average_profile_today": self._boiler_average_profile_today,
            "boiler_average_budget_tomorrow": self._boiler_average_budget_tomorrow,
            "boiler_average_profile_tomorrow": self._boiler_average_profile_tomorrow,
            "boiler_expected_consumption_today": self._boiler_expected_consumption_today,
            "boiler_expected_consumption_tomorrow": self._boiler_expected_consumption_tomorrow,
            "curtailed_pv_today": self._curtailed_pv_today,
            "curtailed_pv_tomorrow": self._curtailed_pv_tomorrow,
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

        # Listen to manual heating cycle updates
        async def handle_manual_heating_update(event):
            self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen("ems_manual_heating_updated", handle_manual_heating_update)
        )

        config = self._entry.data
        options = self._entry.options
        price_buy_sensor_id = options.get(CONF_PRICE_BUY_SENSOR, config.get(CONF_PRICE_BUY_SENSOR))
        price_sell_sensor_id = options.get(CONF_PRICE_SELL_SENSOR, config.get(CONF_PRICE_SELL_SENSOR))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        total_load_consumption_id = options.get(CONF_TOTAL_LOAD_CONSUMPTION, config.get(CONF_TOTAL_LOAD_CONSUMPTION))

        # Recalculate on SOC changes with throttling
        if bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_soc_entity_id], self._async_soc_listener
                )
            )

        # Listen for tariff, consumption and forecast changes
        generic_listeners = []
        vacation_entity_id = options.get(CONF_VACATION_MODE_ENTITY, config.get(CONF_VACATION_MODE_ENTITY))
        if vacation_entity_id:
            generic_listeners.append(vacation_entity_id)
        if price_buy_sensor_id:
            generic_listeners.append(price_buy_sensor_id)
        if price_sell_sensor_id:
            generic_listeners.append(price_sell_sensor_id)
        if total_load_consumption_id:
            generic_listeners.append(total_load_consumption_id)
        generic_listeners.extend([
            "sensor.pv_forecast_today",
            "sensor.pv_forecast_tomorrow",
            "sensor.boiler_dp"
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

            # Break feedback loop: if the event comes from sensor.boiler_dp, only
            # trigger if the schedule content (planning fields) has actually changed.
            # sensor.boiler_dp always re-writes last_calculation/calculation_duration,
            # so attribute-level changes alone must NOT re-trigger sensor.dp.
            if entity_id == "sensor.boiler_dp":
                def _boiler_sched_key(slot):
                    if not isinstance(slot, dict):
                        return ()
                    return (
                        slot.get("date"),
                        slot.get("hour"),
                        slot.get("mode"),
                        slot.get("energy"),
                    )

                old_sched = (
                    old_state.attributes.get("schedule")
                    if old_state is not None
                    else None
                )
                new_sched = new_state.attributes.get("schedule")

                if old_sched is not None and isinstance(old_sched, list) and isinstance(new_sched, list):
                    if [_boiler_sched_key(s) for s in old_sched] == [_boiler_sched_key(s) for s in new_sched]:
                        ems_log(
                            self.hass,
                            _LOGGER,
                            logging.DEBUG,
                            "EMS DP: boiler_dp schedule unchanged — skipping re-calculation"
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
            for key in required_keys:
                entity_id = options.get(key, config.get(key))
                if not entity_id:
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.WARNING,
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
                        logging.WARNING,
                        f"Required sensor '{entity_id}' is in state '{state_obj.state if state_obj else 'None'}'. Skipping strategy update."
                    )
                    self._state = "unavailable"
                    self._error_msg = f"Sensor '{entity_id}' is unavailable"
                    self.async_write_ha_state()
                    return

            fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))
            min_sell_price = storage.min_sell_price
            min_discharge_price = storage.min_discharge_price
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

            # Check bat_emergency: if SOC is at or below emergency threshold, force bat_emergency state
            bat_soc_emergency_val = options.get("bat_soc_emergency", config.get("bat_soc_emergency", 10.0))
            try:
                bat_soc_emergency_val = float(bat_soc_emergency_val)
            except (ValueError, TypeError):
                bat_soc_emergency_val = 10.0

            if isinstance(soc, (int, float)) and soc <= bat_soc_emergency_val:
                ems_log(
                    self.hass,
                    _LOGGER,
                    logging.WARNING,
                    f"Battery SOC {soc}% is at or below emergency threshold {bat_soc_emergency_val}%. Forcing 'bat_emergency' state."
                )
                self._last_calc_soc = soc  # prevent listener from getting stuck
                self._state = "bat_emergency"
                self._error_msg = None
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
            boiler_today = [0.0] * 24
            boiler_tomorrow = [0.0] * 24

            expected_consumption_today = 0.0
            expected_consumption_tomorrow = 0.0

            # Шаг 2 Плана А: Сбор фактически запланированного потребления бойлера из sensor.boiler_dp
            planned_boiler_today = [0.0] * 24
            planned_boiler_tomorrow = [0.0] * 24
            boiler_dp_state = self.hass.states.get("sensor.boiler_dp")
            ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: boiler_dp_state found=%s state=%s", boiler_dp_state is not None, boiler_dp_state.state if boiler_dp_state else "None")
            if boiler_dp_state and boiler_dp_state.state not in (None, "unknown", "unavailable"):
                boiler_schedule = boiler_dp_state.attributes.get("schedule", []) or []
                ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: boiler_schedule length=%d", len(boiler_schedule))
                now = dt_util.now()
                today_str = now.strftime("%Y-%m-%d")
                tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                for slot in boiler_schedule:
                    slot_date = slot.get("date")
                    slot_hour = slot.get("hour")
                    if slot_date and slot_hour is not None:
                        try:
                            slot_hour = int(slot_hour)
                        except (ValueError, TypeError):
                            continue
                        if 0 <= slot_hour < 24:
                            mode = slot.get("mode", "IDLE")
                            try:
                                energy = float(slot.get("energy", 0.0) or 0.0)
                            except (ValueError, TypeError):
                                energy = 0.0
                            
                            planned_kwh = 0.0
                            if mode in ("ELEC", "ELEC_PUMP"):
                                planned_kwh = energy
                            elif mode in ("PUMP_ONLY", "GAS_PUMP"):
                                planned_kwh = 0.1
                            
                            if slot_date == today_str:
                                planned_boiler_today[slot_hour] = planned_kwh
                                expected_consumption_today += energy
                            elif slot_date == tomorrow_str:
                                planned_boiler_tomorrow[slot_hour] = planned_kwh
                                expected_consumption_tomorrow += energy
                ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: planned_boiler_today sum=%.2fkWh, planned_boiler_tomorrow sum=%.2fkWh", sum(planned_boiler_today), sum(planned_boiler_tomorrow))

            self._boiler_expected_consumption_today = expected_consumption_today
            self._boiler_expected_consumption_tomorrow = expected_consumption_tomorrow

            from homeassistant.helpers import entity_registry as _er_mod
            _load_registry = _er_mod.async_get(self.hass)
            _load_entity_id = _load_registry.async_get_entity_id("sensor", DOMAIN, f"{self._entry_id}_load_consumption") or "sensor.load_consumption"
            load_state = self.hass.states.get(_load_entity_id)
            ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: load_consumption entity=%s found=%s", _load_entity_id, load_state is not None)
            self._boiler_average_budget_today = 0.0
            self._boiler_average_profile_today = [0.0] * 24
            self._boiler_average_budget_tomorrow = 0.0
            self._boiler_average_profile_tomorrow = [0.0] * 24
            if load_state:
                raw_today = load_state.attributes.get("average_today", [fallback_consumption] * 24)
                now = dt_util.now()
                tomorrow_weekday = (now + timedelta(days=1)).weekday()
                day_keys = [
                    "average_monday", "average_tuesday", "average_wednesday",
                    "average_thursday", "average_friday", "average_saturday",
                    "average_sunday",
                ]
                tomorrow_key = day_keys[tomorrow_weekday]
                raw_tomorrow = load_state.attributes.get(tomorrow_key, [fallback_consumption] * 24)

                boiler_today = load_state.attributes.get("boiler_average_today", [0.0] * 24)
                boiler_tomorrow = load_state.attributes.get(f"boiler_{tomorrow_key}", [0.0] * 24)

                clean_boiler_today = safe_float_list(boiler_today, 0.0)
                clean_boiler_tomorrow = safe_float_list(boiler_tomorrow, 0.0)
                self._boiler_average_profile_today = clean_boiler_today
                self._boiler_average_budget_today = sum(clean_boiler_today)
                self._boiler_average_profile_tomorrow = clean_boiler_tomorrow
                self._boiler_average_budget_tomorrow = sum(clean_boiler_tomorrow)

            vacation_entity_id = options.get(CONF_VACATION_MODE_ENTITY) or config.get(CONF_VACATION_MODE_ENTITY)
            vacation_mode = False
            if vacation_entity_id:
                _vac_state = self.hass.states.get(vacation_entity_id)
                if _vac_state is not None:
                    vacation_mode = (_vac_state.state == "on")
            self._vacation_mode = vacation_mode

            if vacation_mode:
                if len(raw_today) == 24 and len(boiler_today) == 24:
                    consumption_today = [max(0.0, float(c) - float(b)) for c, b in zip(raw_today, boiler_today)]
                else:
                    consumption_today = raw_today

                if len(raw_tomorrow) == 24 and len(boiler_tomorrow) == 24:
                    consumption_tomorrow = [max(0.0, float(c) - float(b)) for c, b in zip(raw_tomorrow, boiler_tomorrow)]
                else:
                    consumption_tomorrow = raw_tomorrow
                
                ems_log(
                    self.hass,
                    _LOGGER,
                    logging.INFO,
                    "EMS DP: Vacation mode is ENABLED. Net house load used (boiler average subtracted: sum_today=%.2f, sum_tomorrow=%.2f)",
                    sum(boiler_today),
                    sum(boiler_tomorrow)
                )
            else:
                if len(raw_today) == 24 and len(boiler_today) == 24:
                    consumption_today = raw_today
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.DEBUG,
                        "EMS DP: Using total average load profile today (sum: %.2fkWh, boiler average portion: %.2fkWh)",
                        sum(raw_today),
                        sum(boiler_today)
                    )
                else:
                    consumption_today = raw_today

                if len(raw_tomorrow) == 24 and len(boiler_tomorrow) == 24:
                    consumption_tomorrow = raw_tomorrow
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.DEBUG,
                        "EMS DP: Using total average load profile tomorrow (sum: %.2fkWh, boiler average portion: %.2fkWh)",
                        sum(raw_tomorrow),
                        sum(boiler_tomorrow)
                    )
                else:
                    consumption_tomorrow = raw_tomorrow

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

            self._reactive_debounce_time = now
            result = await self.hass.async_add_executor_job(
                self._calculate_strategy_sync,
                effective_soc,
                capacity,
                min_bat_soc,
                bat_max_power,
                min_sell_price,
                min_discharge_price,
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
                boiler_today,
                boiler_tomorrow,
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
            self._curtailed_pv_today = result.get("curtailed_pv_today", 0.0)
            self._curtailed_pv_tomorrow = result.get("curtailed_pv_tomorrow", 0.0)

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
        min_discharge_price: float,
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
        planned_boiler_today: list[float] = None,
        planned_boiler_tomorrow: list[float] = None,
    ) -> dict[str, Any]:
        """Build grid of slots and call DP core helper."""
        from .dp_engine import run_unified_dp, DPConfig
        from .const import INVERTER_MODES

        if planned_boiler_today is None:
            planned_boiler_today = [0.0] * 24
        if planned_boiler_tomorrow is None:
            planned_boiler_tomorrow = [0.0] * 24

        sum_curtailed_today = 0.0
        sum_curtailed_tomorrow = 0.0

        now = dt_util.now()
        current_hour = now.hour

        now_minute = now.minute
        now_second = now.second
        remaining_seconds = (59 - now_minute) * 60 + (60 - now_second)
        remaining_hour_fraction = max(remaining_seconds / 3600.0, 1 / 3600.0)
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
                "planned_boiler_kwh": float(planned_boiler_today[h]) if (h < len(planned_boiler_today) and planned_boiler_today[h] is not None) else 0.0,
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
                    "planned_boiler_kwh": float(planned_boiler_tomorrow[h]) if (h < len(planned_boiler_tomorrow) and planned_boiler_tomorrow[h] is not None) else 0.0,
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
            min_discharge_price=min_discharge_price,
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
                remaining_hour_fraction=remaining_hour_fraction,
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
                    remaining_hour_fraction=remaining_hour_fraction,
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

            _is_override_slot = (
                (action == "discharge" and dis_keys.get(key, {}).get("override", False))
                or (action == "grid_charge" and chg_keys.get(key, {}).get("override", False))
            )
            _map_fn = map_override_to_physical if _is_override_slot else map_dp_to_physical
            physical_mode, mapping_reason = _map_fn(
                action=action,
                sell_price=slot["sell_price"],
                pv_kwh=slot["pv_kwh"],
                min_sell_price=min_sell_price,
                min_discharge_price=min_discharge_price,
                cheap_ahead=cheap_ahead,
            )

            if idx == 0:
                current_action = action

            expected_soc_val = stats.get("expected_trajectory", [])[idx] if idx < len(stats.get("expected_trajectory", [])) else 0.0
            # Calculate curtailed solar energy
            mode_config = INVERTER_MODES.get(physical_mode)
            curtail_active = mode_config.curtail_pv if mode_config else False
            wasted = 0.0
            if curtail_active:
                planned_boiler = float(slot.get("planned_boiler_kwh", 0.0))
                consumption_net = max(0.0, float(slot.get("consumption_kwh", 0.0)) - planned_boiler)
                battery_charge = float(energy) if action in ("pv_charge", "grid_charge") else 0.0
                wasted = max(0.0, float(slot.get("pv_kwh", 0.0)) - consumption_net - battery_charge)
                wasted = round(wasted, 4)

            if slot["date"] == today_str:
                sum_curtailed_today += wasted
            elif slot["date"] == tomorrow_str:
                sum_curtailed_tomorrow += wasted

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
                "expected_soc": expected_soc_val,
                "planned_boiler_kwh": slot["planned_boiler_kwh"],
                "curtailed_pv_kwh": wasted,
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
            "curtailed_pv_today": round(sum_curtailed_today, 2),
            "curtailed_pv_tomorrow": round(sum_curtailed_tomorrow, 2),
        }


class EmsSchedulerSensor(SensorEntity):
    """EMS Scheduler State and Overrides Sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _HYSTERESIS_W = 200
    _BUFFER_WINDOW_S = 180  # 3 minutes

    def __init__(self, entry_id: str, device_name: str, entry: ConfigEntry) -> None:
        """Initialize the scheduler sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry
        self._attr_name = "Scheduler"
        self._attr_unique_id = f"{entry_id}_scheduler"
        self.entity_id = "sensor.scheduler"

        # Rolling 3-minute buffers for dynamic PV/load switching: (timestamp, watts)
        self._pv_buffer: deque = deque()
        self._load_buffer: deque = deque()
        self._dynamic_sale_pv_active: bool = False

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

    # ------------------------------------------------------------------
    # Rolling average helpers
    # ------------------------------------------------------------------
    def _push_buffer(self, buf: deque, value_w: float) -> None:
        """Append timestamped reading and trim entries older than 3 min."""
        now_ts = time.monotonic()
        buf.append((now_ts, value_w))
        cutoff = now_ts - self._BUFFER_WINDOW_S
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    def _avg_buffer(self, buf: deque) -> float | None:
        """Return mean of buffer values, or None if empty."""
        if not buf:
            return None
        return sum(v for _, v in buf) / len(buf)

    def _read_power_w(self, entity_id: str | None) -> float | None:
        """Read a power sensor value and normalise to watts."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in (None, "unknown", "unavailable"):
            return None
        try:
            val = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip()
        if unit.lower() in ("kw",):
            val *= 1000.0
        return val

    def _update_dynamic_switching(self) -> None:
        """Update the dynamic override state based on rolling buffers."""
        dp_state = self.hass.states.get("sensor.dp")
        if dp_state is None or dp_state.state in ("unknown", "unavailable"):
            self._dynamic_sale_pv_active = False
            return

        schedule = dp_state.attributes.get("schedule", [])
        if not schedule:
            self._dynamic_sale_pv_active = False
            return

        base_mode = schedule[0].get("physical_mode", dp_state.state)

        # Dynamic switching is only evaluated if base planned mode is sale_pv_no_bat
        if base_mode != "sale_pv_no_bat":
            self._dynamic_sale_pv_active = False
            return

        avg_pv = self._avg_buffer(self._pv_buffer)
        avg_load = self._avg_buffer(self._load_buffer)

        # Fall back if we don't have enough data
        if avg_pv is None or avg_load is None:
            self._dynamic_sale_pv_active = False
            return

        if self._dynamic_sale_pv_active:
            if avg_pv > avg_load + self._HYSTERESIS_W:
                self._dynamic_sale_pv_active = False
        else:
            if avg_pv < avg_load - self._HYSTERESIS_W:
                self._dynamic_sale_pv_active = True

    @property
    def native_value(self) -> str | None:
        """Return the state of the scheduler (current active mode)."""
        config = self._entry.data
        options = self._entry.options
        from .const import CONF_BAT_SOC_ENTITY, CONF_BAT_SOC_EMERGENCY, DEFAULT_BAT_SOC_EMERGENCY

        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        bat_soc_emergency_val = options.get(CONF_BAT_SOC_EMERGENCY, config.get(CONF_BAT_SOC_EMERGENCY, DEFAULT_BAT_SOC_EMERGENCY))
        try:
            bat_soc_emergency_val = float(bat_soc_emergency_val)
        except (ValueError, TypeError):
            bat_soc_emergency_val = DEFAULT_BAT_SOC_EMERGENCY

        # Read actual SOC
        soc = None
        if bat_soc_entity_id:
            soc_state = self.hass.states.get(bat_soc_entity_id)
            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                try:
                    soc = float(soc_state.state)
                except (ValueError, TypeError):
                    pass

        # If actual SOC is <= emergency threshold, force bat_emergency
        if soc is not None and soc <= bat_soc_emergency_val:
            return "bat_emergency"
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        overrides = storage.get_overrides()

        # Calculate active override for the current hour
        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        active_override = overrides.get(today_str, {}).get(str(current_hour))

        dp_state = self.hass.states.get("sensor.dp")

        if active_override is not None:
            # Parse override action (may be "action:target_soc" format)
            active_override_action = active_override.split(":", 1)[0]
            schedule = dp_state.attributes.get("schedule", []) if dp_state is not None else []
            current_slot = schedule[0] if schedule else {}
            sell_price = current_slot.get("sell_price", 0.0)
            pv_kwh = current_slot.get("pv_kwh", 0.0)

            cheap_ahead = False
            if active_override_action != "self_consume" and schedule:
                horizon_end = min(7, len(schedule))
                for f_idx in range(1, horizon_end):
                    future_p_buy = schedule[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            physical_mode, _ = map_override_to_physical(
                action=active_override_action,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=storage.min_sell_price,
                min_discharge_price=storage.min_discharge_price,
                cheap_ahead=cheap_ahead,
            )
            return physical_mode

        if dp_state is None or dp_state.state in ("unknown", "unavailable"):
            return None

        schedule = dp_state.attributes.get("schedule", [])
        if not schedule:
            return None

        base_mode = schedule[0].get("physical_mode", dp_state.state)

        # Apply precalculated dynamic switching
        if base_mode == "sale_pv_no_bat" and self._dynamic_sale_pv_active:
            return "sale_pv"

        return base_mode

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
            CONF_BAT_SOC_EMERGENCY,
            DEFAULT_BAT_SOC_EMERGENCY,
        )

        bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        bat_voltage_entity_id = options.get(CONF_BAT_VOLTAGE, config.get(CONF_BAT_VOLTAGE))
        min_bat_soc = storage.min_bat_soc
        bat_soc_emergency_val = options.get(CONF_BAT_SOC_EMERGENCY, config.get(CONF_BAT_SOC_EMERGENCY, DEFAULT_BAT_SOC_EMERGENCY))
        try:
            bat_soc_emergency_val = float(bat_soc_emergency_val)
        except (ValueError, TypeError):
            bat_soc_emergency_val = DEFAULT_BAT_SOC_EMERGENCY

        # Retrieve battery max power from config/options
        bat_max_power = options.get(CONF_BAT_MAX_POWER, config.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER))
        try:
            bat_max_power = float(bat_max_power)
        except (ValueError, TypeError):
            bat_max_power = DEFAULT_BAT_MAX_POWER

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

        # For the first slot (current hour), energy_kwh covers only the
        # remaining portion of the hour. Normalize it back to a full-hour rate
        # so that displayed power/amps reflect the actual operating level.
        now_minute = now.minute
        now_second = now.second
        remaining_seconds = (59 - now_minute) * 60 + (60 - now_second)
        remaining_hour_fraction = max(remaining_seconds / 3600.0, 1 / 3600.0)

        for slot_idx, slot in enumerate(schedule):
            action = slot.get("action", "idle")
            energy = slot.get("energy_kwh", 0.0)

            power_w = 0.0
            current_a = 0.0

            # If battery is in emergency state, force first slot to bat_emergency with 0 energy
            if slot_idx == 0 and soc is not None and soc <= bat_soc_emergency_val:
                action = "bat_emergency"
                energy = 0.0
                energy_for_power = 0.0
            else:
                # Normalize first slot energy to full-hour rate for power display
                if slot_idx == 0 and remaining_hour_fraction < 1.0:
                    energy_for_power = energy / remaining_hour_fraction
                    energy_for_power = min(energy_for_power, bat_max_power / 1000.0)
                else:
                    energy_for_power = energy

            if action in ("grid_charge", "pv_charge"):
                end_usable = min(usable_capacity, usable_energy + energy)
                power_w = energy_for_power * 1000.0
                current_a = power_w / safe_voltage
            elif action in ("discharge", "self_consume"):
                end_usable = max(0.0, usable_energy - energy)
                power_w = energy_for_power * 1000.0
                current_a = power_w / safe_voltage
            else:
                end_usable = usable_energy

            # Calculate SOC at the end of the hour
            end_soc = (end_usable / safe_capacity) * 100 + min_bat_soc
            end_soc = max(min_bat_soc, min(100.0, end_soc))

            slot_data = {
                **slot,
                "soc": round(soc if (slot_idx == 0 and soc is not None and soc <= bat_soc_emergency_val) else end_soc, 1),
                "power_w": round(power_w, 1),
                "current_a": round(current_a, 1),
            }
            if slot_idx == 0 and soc is not None and soc <= bat_soc_emergency_val:
                slot_data["action"] = "bat_emergency"
                slot_data["physical_mode"] = "bat_emergency"
            dispatched_plan.append(slot_data)
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
            raw_mode = active_override.split(":", 1)[0]
            current_slot = schedule[0] if schedule else {}
            sell_price = current_slot.get("sell_price", 0.0)
            pv_kwh = current_slot.get("pv_kwh", 0.0)

            cheap_ahead = False
            if raw_mode != "self_consume" and schedule:
                horizon_end = min(7, len(schedule))
                for f_idx in range(1, horizon_end):
                    future_p_buy = schedule[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            _, override_reason = map_override_to_physical(
                action=raw_mode,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=storage.min_sell_price,
                min_discharge_price=storage.min_discharge_price,
                cheap_ahead=cheap_ahead,
            )
            mapping_reason = f"override: {active_override} | {override_reason}"

        if soc is not None and soc <= bat_soc_emergency_val:
            raw_mode = "bat_emergency"
            mapping_reason = "battery_emergency"

        current_power = 0.0
        current_amps = 0.0
        current_target_soc = soc

        if dispatched_plan:
            current_slot = dispatched_plan[0]
            current_power = current_slot.get("power_w", 0.0)
            current_amps = current_slot.get("current_a", 0.0)
            current_target_soc = current_slot.get("soc", soc)

        avg_pv = self._avg_buffer(self._pv_buffer)
        avg_load = self._avg_buffer(self._load_buffer)

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
            "avg_pv_w": round(avg_pv, 1) if avg_pv is not None else None,
            "avg_load_w": round(avg_load, 1) if avg_load is not None else None,
            "pv_load_switch_active": self._dynamic_sale_pv_active,
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

        # Subscribe to PV generation and house consumption power sensors
        config = self._entry.data
        options = self._entry.options
        pv_sensor_id = options.get(CONF_CURRENT_PV_GENERATION, config.get(CONF_CURRENT_PV_GENERATION))
        load_sensor_id = options.get(CONF_CURRENT_HOUSE_CONSUMPTION, config.get(CONF_CURRENT_HOUSE_CONSUMPTION))

        if pv_sensor_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [pv_sensor_id], self._async_pv_changed
                )
            )
        if load_sensor_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [load_sensor_id], self._async_load_changed
                )
            )

        # Listen for manual override updates
        self.async_on_remove(
            self.hass.bus.async_listen("ems_schedule_updated", self._async_override_changed)
        )

        # Subscribe to Battery SOC sensor
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        if bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_soc_entity_id], self._async_soc_changed
                )
            )

    async def _async_dp_changed(self, event) -> None:
        """Handle DP sensor changes."""
        self._update_dynamic_switching()
        self.async_write_ha_state()

    async def _async_override_changed(self, event) -> None:
        """Handle manual override updates."""
        self._update_dynamic_switching()
        self.async_write_ha_state()

    async def _async_soc_changed(self, event) -> None:
        """Handle battery SOC changes."""
        self._update_dynamic_switching()
        self.async_write_ha_state()

    async def _async_pv_changed(self, event) -> None:
        """Handle PV generation sensor changes — update rolling buffer."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return
        config = self._entry.data
        options = self._entry.options
        entity_id = options.get(CONF_CURRENT_PV_GENERATION, config.get(CONF_CURRENT_PV_GENERATION))
        val_w = self._read_power_w(entity_id)
        if val_w is not None:
            self._push_buffer(self._pv_buffer, val_w)
        self._update_dynamic_switching()
        self.async_write_ha_state()

    async def _async_load_changed(self, event) -> None:
        """Handle house consumption sensor changes — update rolling buffer."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return
        config = self._entry.data
        options = self._entry.options
        entity_id = options.get(CONF_CURRENT_HOUSE_CONSUMPTION, config.get(CONF_CURRENT_HOUSE_CONSUMPTION))
        val_w = self._read_power_w(entity_id)
        if val_w is not None:
            self._push_buffer(self._load_buffer, val_w)
        self._update_dynamic_switching()
        self.async_write_ha_state()


class EmsTodayProfitSensor(RestoreSensor, SensorEntity):
    """EMS sensor that tracks today's monetary profit."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        device_name: str,
        load_consumption_sensor_id: str,
        grid_export_sensor_id: str,
        grid_import_sensor_id: str,
        price_buy_sensor_id: str,
        price_sell_sensor_id: str,
    ) -> None:
        """Initialize the profit sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._load_consumption_sensor_id = load_consumption_sensor_id
        self._grid_export_sensor_id = grid_export_sensor_id
        self._grid_import_sensor_id = grid_import_sensor_id
        self._price_buy_sensor_id = price_buy_sensor_id
        self._price_sell_sensor_id = price_sell_sensor_id

        self._attr_name = "Today Profit"
        self._attr_unique_id = f"{entry_id}_today_profit"
        self.entity_id = "sensor.today_profit"

        # Internal state tracking
        self._state: float = 0.0
        self._today_import_kwh: float = 0.0
        self._today_import_price: float = 0.0
        self._today_export_kwh: float = 0.0
        self._today_export_price: float = 0.0
        self._today_house_consumption_cost: float = 0.0

        self._last_load_value: float | None = None
        self._last_import_value: float | None = None
        self._last_export_value: float | None = None
        self._last_day: int = dt_util.now().day

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
        """Return today's total profit."""
        return self._state

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        if hasattr(self.hass.config, "currency"):
            return self.hass.config.currency
        return "EUR"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "today_import_kwh": self._today_import_kwh,
            "today_import_price": self._today_import_price,
            "today_export_kwh": self._today_export_kwh,
            "today_export_price": self._today_export_price,
            "today_house_consumption_cost": self._today_house_consumption_cost,
            "last_load_value": self._last_load_value,
            "last_import_value": self._last_import_value,
            "last_export_value": self._last_export_value,
            "last_day": self._last_day,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore historical states."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            try:
                self._state = float(last_state.state)
            except (ValueError, TypeError):
                self._state = 0.0

            # Restore attributes
            self._today_import_kwh = float(last_state.attributes.get("today_import_kwh", 0.0))
            self._today_import_price = float(last_state.attributes.get("today_import_price", 0.0))
            self._today_export_kwh = float(last_state.attributes.get("today_export_kwh", 0.0))
            self._today_export_price = float(last_state.attributes.get("today_export_price", 0.0))
            self._today_house_consumption_cost = float(last_state.attributes.get("today_house_consumption_cost", 0.0))

            self._last_load_value = last_state.attributes.get("last_load_value")
            self._last_import_value = last_state.attributes.get("last_import_value")
            self._last_export_value = last_state.attributes.get("last_export_value")
            self._last_day = last_state.attributes.get("last_day", dt_util.now().day)
        else:
            self._state = 0.0
            self._today_import_kwh = 0.0
            self._today_import_price = 0.0
            self._today_export_kwh = 0.0
            self._today_export_price = 0.0
            self._today_house_consumption_cost = 0.0
            self._last_load_value = None
            self._last_import_value = None
            self._last_export_value = None
            self._last_day = dt_util.now().day

        # Listen to state changes of source sensors
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._load_consumption_sensor_id], self._async_load_changed
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._grid_export_sensor_id], self._async_export_changed
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._grid_import_sensor_id], self._async_import_changed
            )
        )

        # Midnight reset check cron trigger
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_midnight_trigger, hour=0, minute=0, second=0
            )
        )

    async def _async_midnight_trigger(self, datetime_now) -> None:
        """Reset profit metrics at midnight."""
        now = dt_util.now()
        ems_log(self.hass, _LOGGER, logging.INFO, "EMS today profit midnight reset triggered")
        self._reset_today(now.day)
        self.async_write_ha_state()

    def _get_current_prices(self) -> tuple[float, float]:
        """Get current buy and sell prices."""
        buy_price = 0.0
        sell_price = 0.0

        buy_state = self.hass.states.get(self._price_buy_sensor_id)
        if buy_state and buy_state.state not in (None, "unknown", "unavailable"):
            try:
                buy_price = float(buy_state.state)
            except (ValueError, TypeError):
                pass

        sell_state = self.hass.states.get(self._price_sell_sensor_id)
        if sell_state and sell_state.state not in (None, "unknown", "unavailable"):
            try:
                sell_price = float(sell_state.state)
            except (ValueError, TypeError):
                pass

        return buy_price, sell_price

    async def _async_load_changed(self, event) -> None:
        """Handle house consumption sensor updates."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.now()
        if now.day != self._last_day:
            self._reset_today(now.day)

        if self._last_load_value is None:
            self._last_load_value = new_value
            self.async_write_ha_state()
            return

        delta = new_value - self._last_load_value
        if delta < 0:
            self._last_load_value = new_value
            self.async_write_ha_state()
            return

        buy_price, _ = self._get_current_prices()
        self._today_house_consumption_cost = round(self._today_house_consumption_cost + delta * buy_price, 4)
        self._last_load_value = new_value
        self._update_profit()

    async def _async_export_changed(self, event) -> None:
        """Handle grid export sensor updates."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.now()
        if now.day != self._last_day:
            self._reset_today(now.day)

        if self._last_export_value is None:
            self._last_export_value = new_value
            self.async_write_ha_state()
            return

        delta = new_value - self._last_export_value
        if delta < 0:
            self._last_export_value = new_value
            self.async_write_ha_state()
            return

        _, sell_price = self._get_current_prices()
        self._today_export_kwh = round(self._today_export_kwh + delta, 4)
        self._today_export_price = round(self._today_export_price + delta * sell_price, 4)
        self._last_export_value = new_value
        self._update_profit()

    async def _async_import_changed(self, event) -> None:
        """Handle grid import sensor updates."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.now()
        if now.day != self._last_day:
            self._reset_today(now.day)

        if self._last_import_value is None:
            self._last_import_value = new_value
            self.async_write_ha_state()
            return

        delta = new_value - self._last_import_value
        if delta < 0:
            self._last_import_value = new_value
            self.async_write_ha_state()
            return

        buy_price, _ = self._get_current_prices()
        self._today_import_kwh = round(self._today_import_kwh + delta, 4)
        self._today_import_price = round(self._today_import_price + delta * buy_price, 4)
        self._last_import_value = new_value
        self._update_profit()

    def _reset_today(self, current_day: int) -> None:
        """Reset daily accumulators."""
        self._today_import_kwh = 0.0
        self._today_import_price = 0.0
        self._today_export_kwh = 0.0
        self._today_export_price = 0.0
        self._today_house_consumption_cost = 0.0
        self._last_day = current_day
        self._state = 0.0

    def _update_profit(self) -> None:
        """Recalculate profit and write state."""
        self._state = round(
            self._today_house_consumption_cost + self._today_export_price - self._today_import_price,
            4
        )
        self.async_write_ha_state()


class EmsRoiSensor(RestoreSensor, SensorEntity):
    """EMS sensor that tracks cumulative ROI and estimated payback date."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        device_name: str,
        entry,
    ) -> None:
        """Initialize the ROI sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry

        self._attr_name = "ROI"
        self._attr_unique_id = f"{entry_id}_roi"
        self.entity_id = "sensor.roi"

        # Cumulative returned amount (excluding current day)
        self._historical_returned: float = 0.0
        # Today's profit contribution from sensor.today_profit (not yet closed day)
        self._last_today_profit: float = 0.0
        # Daily history list (up to 30 values) for averages
        self._daily_history: list[float] = []
        self._last_day: int = dt_util.now().day

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

    def _get_system_cost(self) -> float:
        """Get current system cost from entry options/data."""
        options = self._entry.options
        data = self._entry.data
        val = options.get(CONF_SYSTEM_COST, data.get(CONF_SYSTEM_COST, DEFAULT_SYSTEM_COST))
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def _compute_state_and_attrs(self) -> tuple[float, dict]:
        """Calculate state (roi_percentage) and attributes."""
        system_cost = self._get_system_cost()
        total_returned = round(self._historical_returned + self._last_today_profit, 4)

        remaining_cost = round(system_cost - total_returned, 4) if system_cost > 0.0 else 0.0
        roi_percentage = round((total_returned / system_cost) * 100, 2) if system_cost > 0.0 else 0.0

        # Average calculations
        if self._daily_history:
            avg_daily = round(sum(self._daily_history) / len(self._daily_history), 4)
        else:
            avg_daily = 0.0

        avg_weekly = round(avg_daily * 7, 4)
        avg_monthly = round(avg_daily * 30, 4)

        # Estimated payback date
        if total_returned >= system_cost and system_cost > 0.0:
            estimated_payback = "Fully Recouped"
        elif avg_daily <= 0.0 or system_cost <= 0.0:
            estimated_payback = "Never"
        else:
            days_needed = remaining_cost / avg_daily
            payback_dt = dt_util.now() + timedelta(days=days_needed)
            estimated_payback = payback_dt.strftime("%Y-%m-%d")

        unit = "EUR"
        if hasattr(self.hass, "config") and hasattr(self.hass.config, "currency"):
            unit = self.hass.config.currency

        attrs = {
            "total_returned": total_returned,
            "remaining_cost": remaining_cost,
            "system_cost": system_cost,
            "roi_percentage": roi_percentage,
            "average_daily_profit": avg_daily,
            "average_weekly_profit": avg_weekly,
            "average_monthly_profit": avg_monthly,
            "estimated_payback_date": estimated_payback,
            "days_in_history": len(self._daily_history),
            "unit": unit,
            # Persistence helpers
            "_historical_returned": self._historical_returned,
            "_daily_history": self._daily_history,
            "_last_day": self._last_day,
        }
        return roi_percentage, attrs

    @property
    def native_value(self) -> float:
        """Return ROI percentage as the sensor state."""
        roi, _ = self._compute_state_and_attrs()
        return roi

    @property
    def native_unit_of_measurement(self) -> str:
        """Return % as unit."""
        return "%"

    @property
    def state_class(self):
        """Return state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        _, attrs = self._compute_state_and_attrs()
        return attrs

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore historical states."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            self._historical_returned = float(last_state.attributes.get("_historical_returned", 0.0))
            raw_history = last_state.attributes.get("_daily_history", [])
            if isinstance(raw_history, list):
                self._daily_history = [float(x) for x in raw_history]
            self._last_day = int(last_state.attributes.get("_last_day", dt_util.now().day))

            # Restore last today_profit from the actual today_profit sensor
            profit_state = self.hass.states.get("sensor.today_profit")
            if profit_state and profit_state.state not in (None, "unknown", "unavailable"):
                try:
                    self._last_today_profit = float(profit_state.state)
                except (ValueError, TypeError):
                    self._last_today_profit = 0.0

        # Track changes to today_profit sensor
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, ["sensor.today_profit"], self._async_profit_changed
            )
        )

        # Midnight trigger to close the day
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_midnight_trigger, hour=0, minute=0, second=0
            )
        )

    async def _async_midnight_trigger(self, datetime_now) -> None:
        """Close current day: archive profit and reset."""
        now = dt_util.now()
        if now.day == self._last_day:
            return

        ems_log(self.hass, _LOGGER, logging.INFO, f"EMS ROI midnight reset: archiving day profit {self._last_today_profit}")

        # Archive yesterday's profit
        self._historical_returned = round(self._historical_returned + self._last_today_profit, 4)
        self._daily_history.append(round(self._last_today_profit, 4))
        # Keep only last 30 days for averages
        if len(self._daily_history) > 30:
            self._daily_history = self._daily_history[-30:]

        self._last_today_profit = 0.0
        self._last_day = now.day
        self.async_write_ha_state()

    async def _async_profit_changed(self, event) -> None:
        """Handle sensor.today_profit state changes."""
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            new_profit = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.now()
        # Handle day rollover in case midnight trigger lagged
        if now.day != self._last_day:
            self._historical_returned = round(self._historical_returned + self._last_today_profit, 4)
            self._daily_history.append(round(self._last_today_profit, 4))
            if len(self._daily_history) > 30:
                self._daily_history = self._daily_history[-30:]
            self._last_today_profit = 0.0
            self._last_day = now.day

        self._last_today_profit = new_profit
        self.async_write_ha_state()


class EmsBoilerCalibrationSensor(RestoreSensor, SensorEntity):
    """EMS sensor that tracks boiler calibration status and stores calibration coefficients."""

    _attr_has_entity_name = True

    def __init__(self, entry_id: str, device_name: str) -> None:
        """Initialize the calibration sensor."""
        self._entry_id = entry_id
        self._device_name = device_name

        self._attr_name = "Boiler Calibration"
        self._attr_unique_id = f"{entry_id}_boiler_calibration"
        self.entity_id = "sensor.boiler_calibration"

        # Default states
        self._state: str = "idle"
        self._gas_only = {"efficiency_c_per_m3": 0.0, "last_calibrated": None}
        self._gas_with_pump = {"efficiency_c_per_m3": 0.0, "last_calibrated": None}
        self._elec_only = {"efficiency_c_per_kwh": 0.0, "heater_power_kw": 2.5, "last_calibrated": None}
        self._elec_with_pump = {"efficiency_c_per_kwh": 0.0, "heater_power_kw": 2.5, "last_calibrated": None}

        # Newton's Law LUT: 11 температурных брэкетов 5°C для каждого бойлера
        _lut_brackets = [
            "75_70", "70_65", "65_60", "60_55", "55_50",
            "50_45", "45_40", "40_35", "35_30", "30_25", "25_20",
        ]
        self._standby_losses = {
            "gas":  {k: {"value": v, "updated_at": None} for k, v in STANDBY_LOSSES_PRESETS["gas"].items()},
            "elec": {k: {"value": v, "updated_at": None} for k, v in STANDBY_LOSSES_PRESETS["elec"].items()},
            "last_calibrated": None,
        }
        self._standby_costs = {}  # real-time cost metrics от _async_calculate_costs
        self._calibration_data = {}

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
        """Return the current calibration phase/state."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "last_updated": dt_util.now().isoformat(),
            "gas_only": self._gas_only,
            "gas_with_pump": self._gas_with_pump,
            "elec_only": self._elec_only,
            "elec_with_pump": self._elec_with_pump,
            "standby_losses": self._standby_losses,
            "standby_costs": self._standby_costs,
            "calibration_data": self._calibration_data,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore historical states."""
        await super().async_added_to_hass()

        # Try to restore from store first
        store = self.hass.data[DOMAIN][self._entry_id].get("calibration_store")
        store_loaded = False
        if store:
            data = store.get_all()
            # If we have some actual calibration done, load them
            has_data = any(
                data[k].get("last_calibrated") is not None
                for k in ["gas_only", "gas_with_pump", "elec_only", "elec_with_pump", "standby_losses"]
            )
            if has_data:
                self._gas_only = data.get("gas_only", self._gas_only)
                self._gas_with_pump = data.get("gas_with_pump", self._gas_with_pump)
                self._elec_only = data.get("elec_only", self._elec_only)
                self._elec_with_pump = data.get("elec_with_pump", self._elec_with_pump)
                stored_sl = data.get("standby_losses", {})
                self._standby_losses = self._migrate_standby_losses(stored_sl)
                store_loaded = True
                store.update_phase("standby_losses", self._standby_losses)
                self.hass.async_create_task(store.async_save())

        last_state = await self.async_get_last_state()
        if last_state:
            self._state = "idle"
            attrs = last_state.attributes
            if "calibration_data" in attrs:
                self._calibration_data = attrs["calibration_data"]

            # If store didn't have calibrated data, fallback to last_state (which may migrate old states)
            if not store_loaded:
                if "gas_only" in attrs:
                    self._gas_only = attrs["gas_only"]
                if "gas_with_pump" in attrs:
                    self._gas_with_pump = attrs["gas_with_pump"]
                if "elec_only" in attrs:
                    self._elec_only = attrs["elec_only"]
                if "elec_with_pump" in attrs:
                    self._elec_with_pump = attrs["elec_with_pump"]
                if "standby_losses" in attrs:
                    self._standby_losses = self._migrate_standby_losses(attrs["standby_losses"])

                # Populate the store so it is saved for future loads
                if store:
                    store.update_phase("gas_only", self._gas_only)
                    store.update_phase("gas_with_pump", self._gas_with_pump)
                    store.update_phase("elec_only", self._elec_only)
                    store.update_phase("elec_with_pump", self._elec_with_pump)
                    store.update_phase("standby_losses", self._standby_losses)
                    self.hass.async_create_task(store.async_save())

        # Register reference in controller if available
        controller = self.hass.data[DOMAIN][self._entry_id].get("boiler_controller")
        if controller:
            controller.calibration_sensor = self
            if self._calibration_data and self._calibration_data.get("phase"):
                self.hass.async_create_task(controller.async_recover_calibration(self._calibration_data))

    # -------------------------------------------------------------------------
    # Public helpers for BoilerController
    # -------------------------------------------------------------------------

    def get_standby_losses(self) -> dict:
        """Возвращает текущую LUT тепловых потерь (gas/elec брэкеты)."""
        return self._standby_losses

    def get_gas_efficiency(self) -> float | None:
        """Возвращает коэффициент газового бойлера °C/m³ (из лучшей калибровки)."""
        # Предпочитаем gas_with_pump как более точный (учитывает оба бойлера)
        eff = self._gas_with_pump.get("efficiency_c_per_m3", 0.0)
        if not eff:
            eff = self._gas_only.get("efficiency_c_per_m3", 0.0)
        return float(eff) if eff else None

    def update_standby_costs(self, costs: dict) -> None:
        """Обновляет real-time метрики стоимости стендбай и записывает в HA."""
        self._standby_costs.update(costs)
        self.async_write_ha_state()

    @staticmethod
    def _migrate_standby_losses(data: dict) -> dict:
        """Мигрирует старый формат и выполняет умное слияние с пресетными значениями.

        Поддерживает три формата входных данных:
        - Старый flat float: {"gas_hourly_loss_c": X, "elec_hourly_loss_c": Y}
        - Старый LUT float: {"gas": {"75_70": 4.36, ...}, "elec": {...}}
        - Новый LUT dict: {"gas": {"75_70": {"value": 4.36, "updated_at": "..."}}, ...}
        """
        _lut_brackets = [
            "75_70", "70_65", "65_60", "60_55", "55_50",
            "50_45", "45_40", "40_35", "35_30", "30_25", "25_20",
        ]
        if "gas" in data and isinstance(data["gas"], dict):
            gas_lut = data["gas"]
            elec_lut = data.get("elec", {})
        else:
            # Очень старый flat-формат — разворачиваем в LUT
            old_gas  = float(data.get("gas_hourly_loss_c",  0.0))
            old_elec = float(data.get("elec_hourly_loss_c", 0.0))
            gas_lut  = {b: old_gas  for b in _lut_brackets}
            elec_lut = {b: old_elec for b in _lut_brackets}

        def _extract_bracket(raw_val, preset_val: float) -> dict:
            """Возвращает брэкет в новом формате {value, updated_at}."""
            if isinstance(raw_val, dict):
                # Уже новый формат
                extracted = float(raw_val.get("value", 0.0))
                updated_at = raw_val.get("updated_at")
            else:
                extracted = float(raw_val) if raw_val is not None else 0.0
                updated_at = None
            value = extracted if extracted > 0.0 else preset_val
            return {"value": value, "updated_at": updated_at}

        merged_gas = {}
        for b in _lut_brackets:
            merged_gas[b] = _extract_bracket(
                gas_lut.get(b), float(STANDBY_LOSSES_PRESETS["gas"][b])
            )

        merged_elec = {}
        for b in _lut_brackets:
            merged_elec[b] = _extract_bracket(
                elec_lut.get(b), float(STANDBY_LOSSES_PRESETS["elec"][b])
            )

        return {
            "gas":  merged_gas,
            "elec": merged_elec,
            "last_calibrated": data.get("last_calibrated"),
        }

    def update_calibration_coefficient(self, phase: str, data: dict) -> None:
        """Update coefficients in the sensor and write state to HA."""
        store_phase = phase
        if phase == "gas_only":
            self._gas_only.update(data)
        elif phase == "gas_with_pump":
            self._gas_with_pump.update(data)
        elif phase == "elec_only":
            self._elec_only.update(data)
        elif phase == "elec_with_pump":
            self._elec_with_pump.update(data)
        elif phase == "overnight_loss":
            # Вложенное слияние: обновляем только переданные брэкеты
            import copy
            date_str = data.get("last_calibrated") or dt_util.now().date().isoformat()
            
            # Форматируем и сливаем брэкеты газа
            if "gas" in data and isinstance(data["gas"], dict):
                self._standby_losses.setdefault("gas", {})
                for k, v in data["gas"].items():
                    if isinstance(v, dict):
                        try:
                            val = float(v.get("value", 0.0))
                        except (ValueError, TypeError):
                            val = float(self._standby_losses["gas"].get(k, {}).get("value", 0.0))
                        self._standby_losses["gas"][k] = {
                            "value": val,
                            "updated_at": v.get("updated_at") or date_str
                        }
                    else:
                        try:
                            val = float(v)
                        except (ValueError, TypeError):
                            val = float(self._standby_losses["gas"].get(k, {}).get("value", 0.0))
                        self._standby_losses["gas"][k] = {
                            "value": val,
                            "updated_at": date_str
                        }
                        
            # Форматируем и сливаем брэкеты электричества
            if "elec" in data and isinstance(data["elec"], dict):
                self._standby_losses.setdefault("elec", {})
                for k, v in data["elec"].items():
                    if isinstance(v, dict):
                        try:
                            val = float(v.get("value", 0.0))
                        except (ValueError, TypeError):
                            val = float(self._standby_losses["elec"].get(k, {}).get("value", 0.0))
                        self._standby_losses["elec"][k] = {
                            "value": val,
                            "updated_at": v.get("updated_at") or date_str
                        }
                    else:
                        try:
                            val = float(v)
                        except (ValueError, TypeError):
                            val = float(self._standby_losses["elec"].get(k, {}).get("value", 0.0))
                        self._standby_losses["elec"][k] = {
                            "value": val,
                            "updated_at": date_str
                        }
                        
            if "last_calibrated" in data:
                self._standby_losses["last_calibrated"] = data["last_calibrated"]
                
            store_phase = "standby_losses"
            data = copy.deepcopy(self._standby_losses)

        # Update in store and persist to disk immediately
        store = self.hass.data[DOMAIN][self._entry_id].get("calibration_store")
        if store:
            store.update_phase(store_phase, data)
            self.hass.async_create_task(store.async_save())

        self.async_write_ha_state()

    def set_calibration_state(self, state: str, calibration_data: dict = None) -> None:
        """Set active calibration phase and update transient data."""
        self._state = state
        self._calibration_data = calibration_data or {}
        self.async_write_ha_state()


class EmsDiagnosticSensor(SensorEntity):
    """EMS Diagnostic Sensor that monitors boiler temperature sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry_id: str, device_name: str, entry: ConfigEntry) -> None:
        """Initialize the diagnostic sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry
        self._attr_name = "EMS Diagnostic"
        self._attr_unique_id = f"{entry_id}_ems_diagnostic"
        self.entity_id = "sensor.ems_diagnostic"

        self._state: str = "OK"
        self._gas_boiler_temp_sensor: str | None = None
        self._gas_boiler_temp_state: str = "missing"
        self._gas_boiler_temp: float | None = None
        self._gas_boiler_effective_volume: float = 0.0

        self._elec_boiler_temp_sensor: str | None = None
        self._elec_boiler_temp_state: str = "missing"
        self._elec_boiler_temp: float | None = None
        self._elec_boiler_effective_volume: float = 0.0

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
        """Return the current diagnostic state."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {
            "gas_boiler_temp_sensor": self._gas_boiler_temp_sensor,
            "gas_boiler_temp_state": self._gas_boiler_temp_state,
            "gas_boiler_temp": self._gas_boiler_temp,
            "gas_boiler_effective_volume": self._gas_boiler_effective_volume,
            "elec_boiler_temp_sensor": self._elec_boiler_temp_sensor,
            "elec_boiler_temp_state": self._elec_boiler_temp_state,
            "elec_boiler_temp": self._elec_boiler_temp,
            "elec_boiler_effective_volume": self._elec_boiler_effective_volume,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()
        config = self._entry.data
        options = self._entry.options
        gas_sensor = options.get("gas_boiler_climate", config.get("gas_boiler_climate"))
        elec_sensor = options.get("elec_boiler_temp", config.get("elec_boiler_temp"))

        listeners = []
        if gas_sensor:
            listeners.append(gas_sensor)
        if elec_sensor:
            listeners.append(elec_sensor)

        if listeners:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, listeners, self._async_sensor_changed
                )
            )

        self._update_state()

    async def _async_sensor_changed(self, event) -> None:
        """Handle temperature sensor updates."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update sensor state and attributes based on temperature sensor availability."""
        config = self._entry.data
        options = self._entry.options

        gas_sensor = options.get("gas_boiler_climate", config.get("gas_boiler_climate"))
        elec_sensor = options.get("elec_boiler_temp", config.get("elec_boiler_temp"))
        gas_cap = float(options.get("gas_boiler_capacity", config.get("gas_boiler_capacity", 100.0)))
        elec_cap = float(options.get("elec_boiler_capacity", config.get("elec_boiler_capacity", 100.0)))

        gas_ok = False
        gas_temp_val = None
        gas_temp_state = "missing"

        if gas_sensor:
            state = self.hass.states.get(gas_sensor)
            if state and state.state not in (None, "unknown", "unavailable"):
                temp_attr = state.attributes.get("current_temperature")
                if temp_attr is not None:
                    try:
                        gas_temp_val = float(temp_attr)
                        gas_ok = True
                        gas_temp_state = "available"
                    except (ValueError, TypeError):
                        gas_temp_state = "invalid_value"
                else:
                    gas_temp_state = "missing_temperature_attribute"
            else:
                gas_temp_state = "unavailable"

        elec_ok = False
        elec_temp_val = None
        elec_temp_state = "missing"

        if elec_sensor:
            state = self.hass.states.get(elec_sensor)
            if state and state.state not in (None, "unknown", "unavailable"):
                try:
                    elec_temp_val = float(state.state)
                    elec_ok = True
                    elec_temp_state = "available"
                except (ValueError, TypeError):
                    elec_temp_state = "invalid_value"
            else:
                elec_temp_state = "unavailable"

        self._gas_boiler_temp_sensor = gas_sensor
        self._gas_boiler_temp_state = gas_temp_state
        self._gas_boiler_temp = gas_temp_val
        self._gas_boiler_effective_volume = gas_cap if gas_ok else 0.0

        self._elec_boiler_temp_sensor = elec_sensor
        self._elec_boiler_temp_state = elec_temp_state
        self._elec_boiler_temp = elec_temp_val
        self._elec_boiler_effective_volume = elec_cap if elec_ok else 0.0

        if gas_ok and elec_ok:
            self._state = "OK"
        else:
            self._state = "ERROR"


class EmsBoilerDpSensor(RestoreSensor, SensorEntity):
    """EMS Boiler Dynamic Programming Scheduler Sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry_id: str, device_name: str, entry: ConfigEntry) -> None:
        """Initialize the Boiler DP sensor."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._entry = entry
        self._attr_name = "Boiler DP"
        self._attr_unique_id = f"{entry_id}_boiler_dp"
        self.entity_id = "sensor.boiler_dp"

        self._gas_sensor: str | None = None
        self._elec_sensor: str | None = None

        self._state: str = "IDLE"
        self._recommended_bypass: str = "OFF"
        self._schedule: list[dict] = []
        self._stats: dict = {}
        self._heating_start_hour: int = 0
        self._heating_end_hour: int = 23
        self._boiler_auto_temp_limit: float = 60.0
        self._t_start: float | None = None
        self._t_min: float | None = None
        self._t_max_elec: float | None = None
        self._t_max_gas: float | None = None
        self._vol_elec: float | None = None
        self._vol_gas: float | None = None
        self._gas_cost_m3: float | None = None
        self._last_calc_time: datetime | None = None
        self._last_calc_temp: float | None = None
        self._calc_duration: float | None = None
        self._t_gas: float | None = None
        self._t_elec: float | None = None

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
        """Return the current recommended action."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        controller = self.hass.data[DOMAIN][self._entry_id].get("boiler_controller")
        manual_active = False
        manual_mode = None
        manual_setpoint = None
        gas_heating_delayed = False
        if controller:
            manual_active = getattr(controller, "_manual_heating_active", False)
            manual_mode = getattr(controller, "_manual_heating_mode", None)
            manual_setpoint = getattr(controller, "_manual_heating_setpoint", None)
            gas_heating_delayed = getattr(controller, "_gas_heating_delayed", False)

        return {
            "schedule": self._schedule,
            "stats": self._stats,
            "recommended_bypass": self._recommended_bypass,
            "t_start": self._t_start,
            "t_gas": self._t_gas,
            "t_elec": self._t_elec,
            "t_min": self._t_min,
            "t_max_elec": self._t_max_elec,
            "t_max_gas": self._t_max_gas,
            "vol_elec": self._vol_elec,
            "vol_gas": self._vol_gas,
            "gas_cost_m3": self._gas_cost_m3,
            "last_calculation": self._last_calc_time.isoformat() if self._last_calc_time else None,
            "calculation_duration": self._calc_duration,
            "manual_heating_active": manual_active,
            "manual_heating_mode": manual_mode,
            "manual_heating_setpoint": manual_setpoint,
            "gas_heating_delayed": gas_heating_delayed,
            "heating_start_hour": self._heating_start_hour,
            "heating_end_hour": self._heating_end_hour,
            "vacation_mode": self._vacation_mode,
            "boiler_auto_temp_limit": self._boiler_auto_temp_limit,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition and restore state."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state
            attrs = last_state.attributes
            self._schedule = attrs.get("schedule", [])
            self._stats = attrs.get("stats", {})
            self._recommended_bypass = attrs.get("recommended_bypass", "OFF")
            self._t_start = attrs.get("t_start")
            self._heating_start_hour = attrs.get("heating_start_hour", 0)
            self._heating_end_hour = attrs.get("heating_end_hour", 23)
            try:
                self._boiler_auto_temp_limit = float(attrs.get("boiler_auto_temp_limit", 60.0))
            except (ValueError, TypeError):
                self._boiler_auto_temp_limit = 60.0
            self._t_min = attrs.get("t_min")
            self._t_max_elec = attrs.get("t_max_elec")
            self._t_max_gas = attrs.get("t_max_gas")
            self._vol_elec = attrs.get("vol_elec")
            self._vol_gas = attrs.get("vol_gas")
            self._gas_cost_m3 = attrs.get("gas_cost_m3")
            self._t_gas = attrs.get("t_gas")
            self._t_elec = attrs.get("t_elec")
            if attrs.get("last_calculation"):
                try:
                    self._last_calc_time = datetime.fromisoformat(attrs["last_calculation"])
                except (ValueError, TypeError):
                    self._last_calc_time = None

        # Listen to target state changes
        config = self._entry.data
        options = self._entry.options
        self._gas_sensor = options.get("gas_boiler_climate", config.get("gas_boiler_climate"))
        self._elec_sensor = options.get("elec_boiler_temp", config.get("elec_boiler_temp"))

        listeners = [
            "sensor.dp",
            "sensor.boiler_calibration",
            "number.ems_boiler_heating_start_hour",
            "number.ems_boiler_heating_end_hour",
            "number.ems_boiler_auto_temp_limit",
        ]
        vacation_entity_id = options.get(CONF_VACATION_MODE_ENTITY, config.get(CONF_VACATION_MODE_ENTITY))
        if vacation_entity_id:
            listeners.append(vacation_entity_id)
        if self._gas_sensor:
            listeners.append(self._gas_sensor)
        if self._elec_sensor:
            listeners.append(self._elec_sensor)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, listeners, self._async_state_changed_listener
            )
        )

        # Recalculate on every hour transition
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

        # Initial trigger
        await self.async_update_boiler_dp(force=True)

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Handle hourly recalculation."""
        await self.async_update_boiler_dp(force=True)

    async def _async_state_changed_listener(self, event) -> None:
        """Handle monitored entity state changes with debouncing."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        # Break feedback loop: if the event comes from sensor.dp, only trigger
        # if the schedule content (planning fields) has actually changed.
        # sensor.dp always re-writes last_calculation/calculation_duration on
        # every run, so attribute-only changes must NOT re-trigger sensor.boiler_dp.
        if entity_id == "sensor.dp":
            def _dp_sched_key(slot):
                if not isinstance(slot, dict):
                    return ()
                return (
                    slot.get("date"),
                    slot.get("hour"),
                    slot.get("buy_price"),
                    slot.get("sell_price"),
                    slot.get("physical_mode"),
                    slot.get("expected_soc"),
                    slot.get("pv_kwh"),
                    slot.get("consumption_kwh"),
                    slot.get("planned_boiler_kwh"),
                    slot.get("action"),
                    slot.get("energy_kwh"),
                )

            old_sched = (
                old_state.attributes.get("schedule")
                if old_state is not None
                else None
            )
            new_sched = (
                new_state.attributes.get("schedule")
                if new_state is not None
                else None
            )

            if old_sched is not None and isinstance(old_sched, list) and isinstance(new_sched, list):
                if [_dp_sched_key(s) for s in old_sched] == [_dp_sched_key(s) for s in new_sched]:
                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.DEBUG,
                        "EMS Boiler DP: sensor.dp schedule unchanged — skipping re-calculation"
                    )
                    return

        force = False
        if entity_id not in (self._gas_sensor, self._elec_sensor):
            force = True
        await self.async_update_boiler_dp(force=force)

    async def async_update_boiler_dp(self, force: bool = False) -> None:
        """Calculate the Boiler DP schedule inside executor."""
        now = dt_util.now()
        config = self._entry.data
        options = self._entry.options

        # 1. Retrieve temperature sensors and calculate current weighted system temp
        gas_sensor = options.get("gas_boiler_climate", config.get("gas_boiler_climate"))
        elec_sensor = options.get("elec_boiler_temp", config.get("elec_boiler_temp"))

        gas_cap = float(options.get("gas_boiler_capacity", config.get("gas_boiler_capacity", 100.0)))
        elec_cap = float(options.get("elec_boiler_capacity", config.get("elec_boiler_capacity", 100.0)))

        # Evaluate availability of gas temp
        gas_ok = False
        t_gas = 0.0
        if gas_sensor:
            state = self.hass.states.get(gas_sensor)
            if state and state.state not in (None, "unknown", "unavailable"):
                temp_attr = state.attributes.get("current_temperature")
                if temp_attr is not None:
                    try:
                        t_gas = float(temp_attr)
                        gas_ok = True
                    except (ValueError, TypeError):
                        pass

        # Evaluate availability of elec temp
        elec_ok = False
        t_elec = 0.0
        if elec_sensor:
            state = self.hass.states.get(elec_sensor)
            if state and state.state not in (None, "unknown", "unavailable"):
                try:
                    t_elec = float(state.state)
                    elec_ok = True
                except (ValueError, TypeError):
                    pass

        # Effective volumes (0.0 if sensor unavailable/missing)
        vol_gas = gas_cap if gas_ok else 0.0
        vol_elec = elec_cap if elec_ok else 0.0

        # Weighted temperature calculation
        total_vol = vol_gas + vol_elec
        if total_vol > 0.0:
            t_curr = (t_gas * vol_gas + t_elec * vol_elec) / total_vol
        else:
            t_curr = 20.0  # Safe default if no sensor is available/configured

        vacation_entity_id = options.get(CONF_VACATION_MODE_ENTITY) or config.get(CONF_VACATION_MODE_ENTITY)
        vacation_mode = False
        if vacation_entity_id:
            _vac_state = self.hass.states.get(vacation_entity_id)
            if _vac_state is not None:
                vacation_mode = (_vac_state.state == "on")
        self._vacation_mode = vacation_mode

        if vacation_mode:
            # Populate vacation schedule with IDLE slots
            schedule_list = []
            for h in range(now.hour, 24):
                schedule_list.append({
                    "date": now.strftime("%Y-%m-%d"),
                    "hour": h,
                    "mode": "IDLE",
                    "temp_start": round(t_curr, 2),
                    "temp_end": round(t_curr, 2),
                    "cost": 0.0,
                    "energy": 0.0,
                    "bypass": False,
                })
            # Tomorrow slots if available
            dp_state = self.hass.states.get("sensor.dp")
            dp_schedule = dp_state.attributes.get("schedule", []) if dp_state else []
            tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            for slot in dp_schedule:
                if slot.get("date") == tomorrow_str:
                    schedule_list.append({
                        "date": tomorrow_str,
                        "hour": slot.get("hour"),
                        "mode": "IDLE",
                        "temp_start": round(t_curr, 2),
                        "temp_end": round(t_curr, 2),
                        "cost": 0.0,
                        "energy": 0.0,
                        "bypass": False,
                    })
            
            self._state = "VACATION"
            self._schedule = schedule_list
            self._stats = {
                "mode": "vacation",
                "total_pv_budget_today": 0.0,
                "boiler_used_today": 0.0,
                "remaining_pv_today": 0.0,
            }
            self._recommended_bypass = "OFF"
            self._heating_start_hour = int(self.hass.states.get("number.ems_boiler_heating_start_hour").state if self.hass.states.get("number.ems_boiler_heating_start_hour") else 0)
            self._heating_end_hour = int(self.hass.states.get("number.ems_boiler_heating_end_hour").state if self.hass.states.get("number.ems_boiler_heating_end_hour") else 23)
            self._t_start = round(t_curr, 2)
            self._t_gas = round(t_gas, 2) if 't_gas' in locals() else 0.0
            self._t_elec = round(t_elec, 2) if 't_elec' in locals() else 0.0
            self._t_min = float(options.get("thermostat_set_temp", config.get("thermostat_set_temp", 45.0)))
            self._t_max_elec = float(options.get("elec_boiler_max_temp", config.get("elec_boiler_max_temp", 70.0)))
            self._t_max_gas = float(options.get("gas_boiler_max_temp", config.get("gas_boiler_max_temp", 50.0)))
            self._vol_elec = vol_elec
            self._vol_gas = vol_gas
            self._gas_cost_m3 = float(options.get("gas_cost_m3", config.get("gas_cost_m3", 0.0)))
            self._last_calc_time = now
            self._last_calc_temp = t_curr
            self._calc_duration = 0.0
            self.async_write_ha_state()
            ems_log(self.hass, _LOGGER, logging.INFO, "EMS Boiler DP: Vacation mode is ENABLED. Boiler schedule set to IDLE.")
            return

        # 2. Check debounce conditions
        if not force and self._last_calc_time is not None and self._last_calc_temp is not None:
            time_delta = (now - self._last_calc_time).total_seconds()
            temp_delta = abs(t_curr - self._last_calc_temp)
            if time_delta < 3600.0 and temp_delta < 3.0:
                return

        # 3. Retrieve schedule slots from sensor.dp
        dp_state = self.hass.states.get("sensor.dp")
        if not dp_state or dp_state.state in ("unknown", "unavailable"):
            ems_log(self.hass, _LOGGER, logging.WARNING, "EMS Boiler DP: sensor.dp is not available yet.")
            self._state = "IDLE"
            self.async_write_ha_state()
            return

        dp_schedule = dp_state.attributes.get("schedule", [])
        ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS Boiler DP: retrieved dp_schedule length=%d", len(dp_schedule))
        if not dp_schedule:
            ems_log(self.hass, _LOGGER, logging.WARNING, "EMS Boiler DP: sensor.dp has no schedule attribute.")
            self._state = "IDLE"
            self.async_write_ha_state()
            return

        # Convert schedule from sensor.dp back to list of dicts required by run_boiler_dp
        slots = []
        for slot in dp_schedule:
            slots.append({
                "date": slot.get("date"),
                "hour": slot.get("hour"),
                "buy_price": float(slot.get("buy_price", 0.0)),
                "sell_price": float(slot.get("sell_price", 0.0)),
                "physical_mode": slot.get("physical_mode", "idle"),
                "expected_soc": float(slot.get("expected_soc", 50.0)),
                "pv_kwh": float(slot.get("pv_kwh", 0.0)),
                "consumption_kwh": float(slot.get("consumption_kwh", 0.0)),
                "planned_boiler_kwh": float(slot.get("planned_boiler_kwh", 0.0)),
                "action": slot.get("action", "idle"),
                "energy_kwh": float(slot.get("energy_kwh", 0.0)),
            })
        ems_log(
            self.hass,
            _LOGGER,
            logging.DEBUG,
            "EMS Boiler DP: first 3 slots for run_boiler_dp: %s",
            ", ".join([f"H{s['hour']} Cons={s['consumption_kwh']} Boiler={s['planned_boiler_kwh']}" for s in slots[:3]])
        )

        try:
            import json as _json_mod
            with open("/config/ems_debug_slots.json", "w", encoding="utf-8") as f:
                _json_mod.dump({
                    "timestamp": now.isoformat(),
                    "boiler_dp_state": self._state,
                    "boiler_dp_schedule": self._schedule,
                    "slots_passed": slots
                }, f, indent=2)
        except Exception as e:
            _LOGGER.warning("EMS Boiler DP: failed to write debug dump: %s", e)

        # 4. Retrieve calibration coefficients from sensor.boiler_calibration
        cal_state = self.hass.states.get("sensor.boiler_calibration")
        if not cal_state or cal_state.state in ("unknown", "unavailable"):
            ems_log(self.hass, _LOGGER, logging.WARNING, "EMS Boiler DP: sensor.boiler_calibration is not available.")
            self._state = "NO CALIB DATA"
            self.async_write_ha_state()
            return

        cal_attrs = cal_state.attributes
        cal_data = {
            "gas_only": cal_attrs.get("gas_only", {}),
            "gas_with_pump": cal_attrs.get("gas_with_pump", {}),
            "elec_only": cal_attrs.get("elec_only", {}),
            "elec_with_pump": cal_attrs.get("elec_with_pump", {}),
            "standby_losses": cal_attrs.get("standby_losses", {}),
        }

        # 5. Extract limits and settings
        t_min = float(options.get(CONF_THERMOSTAT_SET_TEMP, config.get(CONF_THERMOSTAT_SET_TEMP, DEFAULT_THERMOSTAT_SET_TEMP)))
        storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
        boiler_auto_temp_limit = float(getattr(storage, "boiler_auto_temp_limit", 60.0))

        t_max_elec = float(options.get(CONF_ELEC_BOILER_MAX_TEMP, config.get(CONF_ELEC_BOILER_MAX_TEMP, DEFAULT_ELEC_BOILER_MAX_TEMP)))
        t_max_elec = max(t_min, min(t_max_elec, boiler_auto_temp_limit))

        t_max_gas = float(options.get(CONF_GAS_BOILER_MAX_TEMP, config.get(CONF_GAS_BOILER_MAX_TEMP, DEFAULT_GAS_BOILER_MAX_TEMP)))
        t_max_gas = max(t_min, min(t_max_gas, boiler_auto_temp_limit))

        gas_cost_m3 = float(options.get("gas_cost_m3", config.get("gas_cost_m3", 0.0)))

        # 6. Execute run_boiler_dp in the executor thread pool
        start_time = time.perf_counter()
        try:
            t_gas_val = t_gas if gas_ok else 20.0
            t_elec_val = t_elec if elec_ok else 20.0

            bypass_valve = options.get("bypass_valve", config.get("bypass_valve"))
            bypass_start = False
            if bypass_valve:
                state = self.hass.states.get(bypass_valve)
                if state and state.state == "on":
                    bypass_start = True

            storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
            heating_start_hour = int(round(getattr(storage, "boiler_heating_start_hour", 0.0)))
            heating_end_hour = int(round(getattr(storage, "boiler_heating_end_hour", 23.0)))

            # Parse battery capacity
            capacity = 5.12
            bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
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

            # Calculate actual boiler consumption today
            actual_boiler_today = 0.0
            try:
                from homeassistant.helpers import entity_registry as _er_mod
                _load_registry = _er_mod.async_get(self.hass)
                _load_entity_id = _load_registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{self._entry_id}_load_consumption"
                ) or "sensor.load_consumption"
                load_state = self.hass.states.get(_load_entity_id)
                if load_state:
                    boiler_today = load_state.attributes.get("boiler_today")
                    if isinstance(boiler_today, list):
                        actual_boiler_today = sum(
                            float(x) for x in boiler_today if x is not None
                        )
            except Exception as ex:
                _LOGGER.warning("EMS Boiler DP: failed to calculate actual boiler today: %s", ex)

            from .boiler_dp_engine import run_boiler_dp
            current_action, schedule_list, stats_dict = await self.hass.async_add_executor_job(
                run_boiler_dp,
                slots,
                t_gas_val,
                t_elec_val,
                bypass_start,
                t_min,
                t_max_elec,
                t_max_gas,
                vol_elec,
                vol_gas,
                gas_cost_m3,
                cal_data,
                0.001,  # temp_reward
                heating_start_hour,
                heating_end_hour,
                capacity,
                actual_boiler_today,
            )

            self._state = current_action
            self._schedule = schedule_list
            self._stats = stats_dict
            ems_log(
                self.hass,
                _LOGGER,
                logging.DEBUG,
                "EMS Boiler DP: run_boiler_dp returned action=%s schedule_len=%d",
                current_action,
                len(schedule_list)
            )
            if schedule_list:
                first_bypass = schedule_list[0].get("bypass", False)
                self._recommended_bypass = "ON" if first_bypass else "OFF"
            else:
                self._recommended_bypass = "OFF"
            self._heating_start_hour = heating_start_hour
            self._heating_end_hour = heating_end_hour
            self._boiler_auto_temp_limit = boiler_auto_temp_limit
        except Exception as err:
            ems_log(self.hass, _LOGGER, logging.ERROR, f"Error running boiler DP optimizer: {err}", exc_info=True)
            self._state = "error"
            self._schedule = []
            self._stats = {"error": str(err)}
            self._recommended_bypass = "OFF"

        self._t_start = round(t_curr, 2)
        self._t_gas = round(t_gas_val, 2)
        self._t_elec = round(t_elec_val, 2)
        self._t_min = t_min
        self._t_max_elec = t_max_elec
        self._t_max_gas = t_max_gas
        self._vol_elec = vol_elec
        self._vol_gas = vol_gas
        self._gas_cost_m3 = gas_cost_m3
        self._last_calc_time = now
        self._last_calc_temp = t_curr
        self._calc_duration = round(time.perf_counter() - start_time, 3)

        self.async_write_ha_state()
