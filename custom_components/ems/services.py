"""Services for the Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_BAT_CAPACITY_ENTITY,
    CONF_BAT_SOC_ENTITY,
    CONF_BAT_MAX_POWER,
    CONF_MIN_BAT_SOC,
    CONF_BAT_SOC_EMERGENCY,
    DEFAULT_BAT_MAX_POWER,
    DEFAULT_MIN_BAT_SOC,
    DEFAULT_BAT_SOC_EMERGENCY,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_OVERRIDE = "set_manual_override"
SERVICE_CLEAR_OVERRIDE = "clear_manual_override"
SERVICE_CLEAR_ALL_OVERRIDES = "clear_all_overrides"
SERVICE_START_CALIBRATION = "start_calibration"

SET_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    vol.Required("action"): vol.In([
        "grid_charge", "discharge", "self_consume", "idle",
        "sale_pv", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat"
    ]),
    vol.Optional("target_soc"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
})

CLEAR_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
})

START_CALIBRATION_SCHEMA = vol.Schema({
    vol.Required("phase"): vol.In(["gas_only", "gas_with_pump", "elec_only", "elec_with_pump"]),
    vol.Optional("heating_duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("target_temperature_delta"): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=50.0)),
    vol.Optional("stabilization_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
})

START_MANUAL_HEATING_SCHEMA = vol.Schema({
    vol.Required("mode"): vol.In(["GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]),
    vol.Required("setpoint"): vol.All(vol.Coerce(float), vol.Range(min=20.0, max=85.0)),
})


async def async_setup_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up EMS services."""
    storage = hass.data[DOMAIN][entry.entry_id]["storage"]

    async def handle_set_override(call: ServiceCall) -> None:
        """Handle setting a manual override."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        action = call.data["action"]
        target_soc_raw = call.data.get("target_soc")

        override_value = action

        # For grid_charge / discharge: validate and possibly clamp target_soc by battery power
        if action in ("grid_charge", "discharge") and target_soc_raw is not None:
            target_soc = float(target_soc_raw)

            config = entry.data
            options = entry.options

            bat_capacity_entity_id = options.get(CONF_BAT_CAPACITY_ENTITY, config.get(CONF_BAT_CAPACITY_ENTITY))
            bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
            bat_max_power_w = float(options.get(CONF_BAT_MAX_POWER, config.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER)))
            min_bat_soc = float(storage.min_bat_soc) if hasattr(storage, "min_bat_soc") else DEFAULT_MIN_BAT_SOC

            # Parse battery capacity (kWh)
            capacity_kwh = 5.12
            if bat_capacity_entity_id:
                cap_state = hass.states.get(bat_capacity_entity_id)
                if cap_state and cap_state.state not in (None, "unknown", "unavailable"):
                    try:
                        capacity_kwh = float(cap_state.state)
                        unit = cap_state.attributes.get("unit_of_measurement", "")
                        if unit == "Wh" or capacity_kwh > 100.0:
                            capacity_kwh = capacity_kwh / 1000.0
                    except (ValueError, TypeError):
                        pass

            # Parse current SOC
            current_soc = 50.0
            if bat_soc_entity_id:
                soc_state = hass.states.get(bat_soc_entity_id)
                if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                    try:
                        val = float(soc_state.state)
                        if 0.0 <= val <= 100.0:
                            current_soc = val
                    except (ValueError, TypeError):
                        pass

            # Determine start_soc for the target hour
            now = dt_util.now()
            today_str = now.strftime("%Y-%m-%d")

            if date_str == today_str and hour == now.hour:
                # Current hour: use live SOC and remaining time
                remaining_seconds = (59 - now.minute) * 60 + (60 - now.second)
                duration_h = max(remaining_seconds / 3600.0, 5.0 / 60.0)  # min 5 minutes
                start_soc = current_soc
            else:
                # Future hour: look up preceding slot SOC from scheduler current_plan
                duration_h = 1.0
                start_soc = current_soc  # fallback

                scheduler_state = hass.states.get("sensor.scheduler")
                if scheduler_state and scheduler_state.attributes:
                    current_plan = scheduler_state.attributes.get("current_plan", [])
                    # Find preceding slot: same date if hour > 0, else previous day hour 23
                    if hour > 0:
                        pred_date = date_str
                        pred_hour = hour - 1
                    else:
                        from datetime import datetime, timedelta
                        pred_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
                        pred_date = pred_dt.strftime("%Y-%m-%d")
                        pred_hour = 23
                    for slot in current_plan:
                        if slot.get("date") == pred_date and slot.get("hour") == pred_hour:
                            pred_soc = slot.get("soc")
                            if pred_soc is not None:
                                try:
                                    start_soc = float(pred_soc)
                                except (ValueError, TypeError):
                                    pass
                            break

            # Safety guard
            safe_capacity = max(capacity_kwh, 0.001)
            max_power_kw = bat_max_power_w / 1000.0

            # Calculate required power for the desired SOC change
            soc_delta = abs(target_soc - start_soc)
            req_power_kw = (soc_delta / 100.0 * safe_capacity) / duration_h

            # Clamp target_soc if power exceeds battery max
            if req_power_kw > max_power_kw:
                achievable_delta = (max_power_kw * duration_h / safe_capacity) * 100.0
                if action == "grid_charge":
                    target_soc = round(min(start_soc + achievable_delta, 100.0), 1)
                else:  # discharge
                    target_soc = round(max(start_soc - achievable_delta, min_bat_soc), 1)
                _LOGGER.info(
                    "set_manual_override: required power %.2f kW exceeds max %.2f kW. "
                    "Clamping target_soc to %.1f%%",
                    req_power_kw, max_power_kw, target_soc,
                )
            else:
                target_soc = round(target_soc, 1)

            # Clamp to valid SOC range
            target_soc = max(min_bat_soc, min(100.0, target_soc))
            override_value = f"{action}:{target_soc}"

        await storage.async_set_override(date_str, hour, override_value)
        _LOGGER.debug("Manual override set via service: %s %02d:00 -> %s", date_str, hour, override_value)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_override(call: ServiceCall) -> None:
        """Handle clearing a manual override."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        await storage.async_clear_override(date_str, hour)
        _LOGGER.debug("Manual override cleared via service: %s %02d:00", date_str, hour)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_all_overrides(call: ServiceCall) -> None:
        """Handle clearing all manual overrides."""
        await storage.async_clear_all_overrides()
        _LOGGER.debug("All manual overrides cleared via service")
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_start_calibration(call: ServiceCall) -> None:
        """Handle starting boiler calibration."""
        phase = call.data["phase"]
        heating_duration = call.data.get("heating_duration_minutes")
        target_delta = call.data.get("target_temperature_delta")
        stabilization = call.data.get("stabilization_minutes")

        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_start_calibration(
            phase, 
            heating_duration_minutes=heating_duration,
            target_temperature_delta=target_delta,
            stabilization_minutes=stabilization
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_OVERRIDE, handle_set_override, schema=SET_OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_OVERRIDE, handle_clear_override, schema=CLEAR_OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_ALL_OVERRIDES, handle_clear_all_overrides
    )
    async def handle_start_manual_heating(call: ServiceCall) -> None:
        """Handle starting manual heating."""
        mode = call.data["mode"]
        setpoint = call.data["setpoint"]
        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_start_manual_heating(mode, setpoint)

    async def handle_stop_manual_heating(call: ServiceCall) -> None:
        """Handle stopping manual heating."""
        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_stop_manual_heating()

    hass.services.async_register(
        DOMAIN, SERVICE_START_CALIBRATION, handle_start_calibration, schema=START_CALIBRATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "start_manual_heating", handle_start_manual_heating, schema=START_MANUAL_HEATING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "stop_manual_heating", handle_stop_manual_heating
    )
    _LOGGER.debug("EMS services successfully registered")


def async_unload_services(hass: HomeAssistant) -> None:
    """Unload EMS services."""
    for service in [
        SERVICE_SET_OVERRIDE,
        SERVICE_CLEAR_OVERRIDE,
        SERVICE_CLEAR_ALL_OVERRIDES,
        SERVICE_START_CALIBRATION,
        "start_manual_heating",
        "stop_manual_heating",
    ]:
        try:
            hass.services.async_remove(DOMAIN, service)
        except ValueError:
            pass
    _LOGGER.debug("EMS services unregistered")
