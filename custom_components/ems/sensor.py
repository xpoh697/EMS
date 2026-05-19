"""Sensor platform for EMS integration."""
from __future__ import annotations

import logging
from datetime import datetime
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_TOTAL_LOAD_CONSUMPTION,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_FALLBACK_CONSUMPTION,
)
from .utils import ems_log

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EMS sensors from config entry."""
    config = entry.data
    options = entry.options

    target_sensor_id = options.get(CONF_TOTAL_LOAD_CONSUMPTION, config.get(CONF_TOTAL_LOAD_CONSUMPTION))
    statistics_days = options.get(CONF_STATISTICS_DAYS, config.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS))
    fallback_consumption = options.get(CONF_FALLBACK_CONSUMPTION, config.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))

    if target_sensor_id:
        async_add_entities(
            [
                EmsLoadConsumptionSensor(
                    entry.entry_id,
                    target_sensor_id,
                    statistics_days,
                    fallback_consumption,
                )
            ]
        )


class EmsLoadConsumptionSensor(RestoreSensor, SensorEntity):
    """EMS sensor that tracks today's load consumption and stores weekday profiles."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True

    def __init__(
        self,
        entry_id: str,
        target_sensor_id: str,
        statistics_days: int,
        fallback_consumption: float,
    ) -> None:
        """Initialize the load consumption sensor."""
        self._entry_id = entry_id
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

        attrs = {
            "today": self._today_consumption,
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
        if new_state is None or new_state.state in (None, "unknown", "unavailable"):
            return

        try:
            new_value = float(new_state.state)
        except (ValueError, TypeError):
            return

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
``` chosen option
