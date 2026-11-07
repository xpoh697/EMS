"""Services for the Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
import math
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_BAT_CAPACITY_ENTITY,
    CONF_BAT_SOC_ENTITY,
    CONF_MIN_BAT_SOC,
    CONF_BAT_SOC_EMERGENCY,
    DEFAULT_MIN_BAT_SOC,
    DEFAULT_BAT_SOC_EMERGENCY,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_OVERRIDE = "set_manual_override"
SERVICE_CLEAR_OVERRIDE = "clear_manual_override"
SERVICE_CLEAR_ALL_OVERRIDES = "clear_all_overrides"
SERVICE_START_CALIBRATION = "start_calibration"
SERVICE_RESET_CALIBRATION = "reset_calibration"
SERVICE_RESET_PV_FACTOR = "reset_pv_factor"

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
    vol.Required("mode"): vol.In(["GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP", "PUMP_ONLY"]),
    vol.Required("setpoint"): vol.All(vol.Coerce(float), vol.Range(min=20.0, max=85.0)),
})

RESET_CALIBRATION_SCHEMA = vol.Schema({
    vol.Optional("reset_type", default="all"): vol.In(["all", "heating", "standby"]),
})

SERVICE_SET_BOILER_OVERRIDE = "set_boiler_override"
SERVICE_CLEAR_BOILER_OVERRIDE = "clear_boiler_override"
SERVICE_CLEAR_ALL_BOILER_OVERRIDES = "clear_all_boiler_overrides"

BOILER_MODES = ["IDLE", "GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP", "PUMP_ONLY"]

SET_BOILER_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
    vol.Required("mode"): vol.In(BOILER_MODES),
    vol.Optional("target_temp"): vol.All(vol.Coerce(float), vol.Range(min=20.0, max=85.0)),
})

CLEAR_BOILER_OVERRIDE_SCHEMA = vol.Schema({
    vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{4}-\d{2}-\d{2}$")),
    vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
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

        # For grid_charge / discharge: validate target_soc within valid range [10.0, 100.0]
        if action in ("grid_charge", "discharge") and target_soc_raw is not None:
            try:
                target_soc = max(10.0, min(100.0, round(float(target_soc_raw), 1)))
                override_value = f"{action}:{target_soc}"
            except (ValueError, TypeError):
                override_value = action

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

    async def handle_reset_calibration(call: ServiceCall) -> None:
        """Handle resetting boiler calibration."""
        reset_type = call.data.get("reset_type", "all")
        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_reset_calibration(reset_type)

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_CALIBRATION, handle_reset_calibration, schema=RESET_CALIBRATION_SCHEMA
    )

    async def handle_reset_pv_factor(call: ServiceCall) -> None:
        """Handle resetting PV forecast Layer 2 factor and history."""
        hass.bus.async_fire("ems_reset_pv_factor")
        _LOGGER.info("PV forecast reset event fired via service")

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_PV_FACTOR, handle_reset_pv_factor
    )

    # ── Boiler mode override services ────────────────────────────────────────

    async def handle_set_boiler_override(call: ServiceCall) -> None:
        """Handle setting a boiler mode override for a specific hour."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        mode = call.data["mode"]
        target_temp_raw = call.data.get("target_temp")

        override_val = mode
        if target_temp_raw is not None and mode in ("GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"):
            try:
                target_temp = float(target_temp_raw)
                config = entry.data
                options = entry.options

                t_min = float(options.get("thermostat_set_temp", config.get("thermostat_set_temp", 40.0)))
                if "GAS" in mode:
                    t_max = float(options.get("gas_boiler_max_temp", config.get("gas_boiler_max_temp", 50.0)))
                else:
                    t_max = float(options.get("elec_boiler_max_temp", config.get("elec_boiler_max_temp", 70.0)))
                t_max = min(t_max, 85.0)

                clamped_temp = max(t_min, min(t_max, target_temp))
                override_val = f"{mode}:{clamped_temp:.1f}"
            except (ValueError, TypeError):
                override_val = mode

        await storage.async_set_boiler_override(date_str, hour, override_val)
        _LOGGER.debug("Boiler override set via service: %s %02d:00 -> %s", date_str, hour, override_val)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_boiler_override(call: ServiceCall) -> None:
        """Handle clearing a boiler mode override for a specific hour."""
        date_str = call.data.get("date") or dt_util.now().strftime("%Y-%m-%d")
        hour = call.data["hour"]
        await storage.async_clear_boiler_override(date_str, hour)
        _LOGGER.debug("Boiler override cleared via service: %s %02d:00", date_str, hour)
        hass.bus.async_fire("ems_schedule_updated")

    async def handle_clear_all_boiler_overrides(call: ServiceCall) -> None:
        """Handle clearing all boiler mode overrides."""
        await storage.async_clear_all_boiler_overrides()
        _LOGGER.debug("All boiler overrides cleared via service")
        hass.bus.async_fire("ems_schedule_updated")

    hass.services.async_register(
        DOMAIN, SERVICE_SET_BOILER_OVERRIDE, handle_set_boiler_override,
        schema=SET_BOILER_OVERRIDE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_BOILER_OVERRIDE, handle_clear_boiler_override,
        schema=CLEAR_BOILER_OVERRIDE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_ALL_BOILER_OVERRIDES, handle_clear_all_boiler_overrides,
    )

    _LOGGER.debug("EMS services successfully registered")


def async_unload_services(hass: HomeAssistant) -> None:
    """Unload EMS services."""
    for service in [
        SERVICE_SET_OVERRIDE,
        SERVICE_CLEAR_OVERRIDE,
        SERVICE_CLEAR_ALL_OVERRIDES,
        SERVICE_START_CALIBRATION,
        SERVICE_RESET_CALIBRATION,
        SERVICE_RESET_PV_FACTOR,
        "start_manual_heating",
        "stop_manual_heating",
        SERVICE_SET_BOILER_OVERRIDE,
        SERVICE_CLEAR_BOILER_OVERRIDE,
        SERVICE_CLEAR_ALL_BOILER_OVERRIDES,
    ]:
        try:
            hass.services.async_remove(DOMAIN, service)
        except ValueError:
            pass
    _LOGGER.debug("EMS services unregistered")

