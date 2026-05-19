"""Sensor platform for EMS integration."""
from __future__ import annotations

import logging
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
)
from .utils import ems_log, calculate_battery_degradation, parse_price_sensor
from .dp_engine import run_unified_dp, DPConfig

_LOGGER = logging.getLogger(__name__)

def parse_solcast_forecast(hass: HomeAssistant, state_obj) -> list[float]:
    """Parse Solcast detailedForecast and analysis intervals to compute baseline hourly kWh."""
    hourly_baselines = [0.0] * 24
    if not state_obj:
        return hourly_baselines

    attrs = state_obj.attributes
    detailed_forecast = attrs.get("detailedForecast")
    if not isinstance(detailed_forecast, list):
        # Fallback: if detailedForecast is not present, check if there's a simple state value
        try:
            total_val = float(state_obj.state)
        except (ValueError, TypeError):
            total_val = 0.0
        # Distribute total_val over daylight hours (e.g. 7:00 to 18:00, 12 hours)
        if total_val > 0.0:
            for h in range(7, 19):
                hourly_baselines[h] = round(total_val / 12.0, 4)
        return hourly_baselines

    analysis = attrs.get("analysis", {})
    intervals = []
    if isinstance(analysis, dict):
        intervals = analysis.get("intervals", [])

    # Group slots by local hour
    slot_duration = 0.5
    if len(detailed_forecast) > 0:
        # Try to calculate duration from the first two periods
        try:
            start_str = detailed_forecast[0].get("period_start")
            if len(detailed_forecast) > 1:
                next_start_str = detailed_forecast[1].get("period_start")
                start_dt = dt_util.parse_datetime(start_str)
                next_dt = dt_util.parse_datetime(next_start_str)
                if start_dt and next_dt:
                    slot_duration = abs((next_dt - start_dt).total_seconds()) / 3600.0
        except Exception:
            pass

    for i, slot in enumerate(detailed_forecast):
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
            if len(detailed_forecast) == 48:
                local_hour = min(23, max(0, i // 2))
            elif len(detailed_forecast) == 24:
                local_hour = min(23, max(0, i))
            else:
                continue

        try:
            pv_estimate = float(slot.get("pv_estimate", 0.0))
        except (ValueError, TypeError):
            pv_estimate = 0.0

        try:
            pv_estimate10 = float(slot.get("pv_estimate10", slot.get("estimate10", 0.0)))
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

    return [round(val, 4) for val in hourly_baselines]


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
    bat_voltage_entity_id = options.get(CONF_BAT_VOLTAGE, config.get(CONF_BAT_VOLTAGE))
    min_bat_soc = options.get(CONF_MIN_BAT_SOC, config.get(CONF_MIN_BAT_SOC, DEFAULT_MIN_BAT_SOC))

    # Log errors or info for house consumption / basic settings
    if not target_sensor_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Total load consumption sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Total load consumption sensor configured successfully: {target_sensor_id}")

    if not current_house_consumption_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Current house consumption sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Current house consumption sensor configured successfully: {current_house_consumption_id}")

    if not inverter_modes_list_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Inverter modes list is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Inverter modes list configured successfully: {inverter_modes_list_id}")

    # Log errors or info for PV sensors
    if not current_pv_generation_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Current PV generation sensor is not configured in settings!")
    else:
        ems_log(hass, _LOGGER, logging.INFO, f"Current PV generation sensor configured successfully: {current_pv_generation_id}")

    if not pv_generation_today_id:
        ems_log(hass, _LOGGER, logging.ERROR, "PV generation today sensor is not configured in settings!")
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

    if not bat_cur_power_entity_id:
        ems_log(hass, _LOGGER, logging.ERROR, "Battery current power entity is not configured in settings!")
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

    if bat_cur_power_entity_id and bat_capacity_entity_id:
        entities.append(
            EmsDpSensor(
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

        self._baselines = parse_solcast_forecast(self.hass, state_obj)

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

        self._baselines = parse_solcast_forecast(self.hass, state_obj)
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
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity registry addition."""
        await super().async_added_to_hass()

        # Initial calculation
        await self.async_update_strategy()

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
        bat_cur_power_entity_id = options.get(CONF_BAT_CUR_POWER_ENTITY, config.get(CONF_BAT_CUR_POWER_ENTITY))

        # Recalculate on SOC changes with throttling
        if bat_cur_power_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_cur_power_entity_id], self._async_soc_listener
                )
            )

        # Listen for tariff, forecast and load profile changes
        generic_listeners = []
        if price_buy_sensor_id:
            generic_listeners.append(price_buy_sensor_id)
        if price_sell_sensor_id:
            generic_listeners.append(price_sell_sensor_id)
        generic_listeners.extend([
            "sensor.pv_forecast_today",
            "sensor.pv_forecast_tomorrow",
            "sensor.load_consumption"
        ])

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, generic_listeners, self._async_generic_listener
            )
        )

    async def _async_hourly_trigger(self, datetime_now) -> None:
        """Handle hourly recalculation."""
        ems_log(self.hass, _LOGGER, logging.DEBUG, "EMS DP: hourly recalculation triggered")
        await self.async_update_strategy()

    async def _async_generic_listener(self, event) -> None:
        """Handle changes in generic sensors immediately."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state:
            ems_log(self.hass, _LOGGER, logging.DEBUG, f"EMS DP trigger: update from {entity_id}")
            await self.async_update_strategy()

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

    async def async_update_strategy(self) -> None:
        """Gather states and call DP execution inside executor pool."""
        config = self._entry.data
        options = self._entry.options

        fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))
        min_sell_price = options.get(CONF_MIN_SELL_PRICE, config.get(CONF_MIN_SELL_PRICE, DEFAULT_MIN_SELL_PRICE))
        bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
        bat_cur_power_entity_id = options.get(CONF_BAT_CUR_POWER_ENTITY, config.get(CONF_BAT_CUR_POWER_ENTITY))
        bat_max_power = options.get(CONF_BAT_MAX_POWER, config.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER))
        min_bat_soc = options.get(CONF_MIN_BAT_SOC, config.get(CONF_MIN_BAT_SOC, DEFAULT_MIN_BAT_SOC))

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

        # Parse current SOC
        soc = 50.0
        if bat_cur_power_entity_id:
            soc_state = self.hass.states.get(bat_cur_power_entity_id)
            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                try:
                    soc = float(soc_state.state)
                except (ValueError, TypeError):
                    pass

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

        result = await self.hass.async_add_executor_job(
            self._calculate_strategy_sync,
            soc,
            capacity,
            min_bat_soc,
            bat_max_power,
            min_sell_price,
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

        self.async_write_ha_state()

    def _calculate_strategy_sync(
        self,
        soc: float,
        capacity: float,
        min_bat_soc: float,
        bat_max_power: float,
        min_sell_price: float,
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
            slots.append({
                "date": today_str,
                "hour": h,
                "buy_price": buy_prices_today[h] if h < len(buy_prices_today) else 0.0,
                "sell_price": sell_prices_today[h] if h < len(sell_prices_today) else 0.0,
                "pv_kwh": pv_today[h] if h < len(pv_today) else 0.0,
                "consumption_kwh": consumption_today[h] if h < len(consumption_today) else fallback_consumption,
            })
        # Tomorrow
        has_tomorrow_prices = any(buy_prices_tomorrow) or any(sell_prices_tomorrow)
        if has_tomorrow_prices:
            for h in range(24):
                slots.append({
                    "date": tomorrow_str,
                    "hour": h,
                    "buy_price": buy_prices_tomorrow[h] if h < len(buy_prices_tomorrow) else 0.0,
                    "sell_price": sell_prices_tomorrow[h] if h < len(sell_prices_tomorrow) else 0.0,
                    "pv_kwh": pv_tomorrow[h] if h < len(pv_tomorrow) else 0.0,
                    "consumption_kwh": consumption_tomorrow[h] if h < len(consumption_tomorrow) else fallback_consumption,
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
