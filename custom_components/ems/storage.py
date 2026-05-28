"""Storage helper for EMS schedules and manual overrides."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ems_schedule_{entry_id}"


class EmsScheduleStorage:
    """Manages storage of manual overrides and schedule for EMS."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the storage helper."""
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry_id))
        self._overrides: dict[str, dict[str, str]] = {}  # date -> hour -> action
        self.last_override_change: str | None = None
        self.min_bat_soc: float = 20.0
        self.min_sell_price: float = 0.0
        self.min_discharge_price: float = 0.0
        self.min_energy_to_discharge: float = 0.0
        self.boiler_heating_start_hour: float = 0.0
        self.boiler_heating_end_hour: float = 23.0

    async def async_load(self, entry: ConfigEntry | None = None) -> None:
        """Load data from JSON storage."""
        data = await self._store.async_load()
        
        # Fallbacks from config entry if available (migration of settings)
        fallback_min_bat_soc = 20.0
        fallback_min_sell_price = 0.0
        fallback_min_discharge_price = 0.0
        fallback_min_energy_to_discharge = 0.0
        if entry is not None:
            from .const import (
                CONF_MIN_BAT_SOC,
                CONF_MIN_SELL_PRICE,
                CONF_MIN_DISCHARGE_PRICE,
                CONF_MIN_ENERGY_TO_DISCHARGE,
                DEFAULT_MIN_BAT_SOC,
                DEFAULT_MIN_SELL_PRICE,
                DEFAULT_MIN_DISCHARGE_PRICE,
                DEFAULT_MIN_ENERGY_TO_DISCHARGE,
            )
            fallback_min_bat_soc = entry.options.get(
                CONF_MIN_BAT_SOC, entry.data.get(CONF_MIN_BAT_SOC, DEFAULT_MIN_BAT_SOC)
            )
            fallback_min_sell_price = entry.options.get(
                CONF_MIN_SELL_PRICE, entry.data.get(CONF_MIN_SELL_PRICE, DEFAULT_MIN_SELL_PRICE)
            )
            fallback_min_discharge_price = entry.options.get(
                CONF_MIN_DISCHARGE_PRICE, entry.data.get(CONF_MIN_DISCHARGE_PRICE, DEFAULT_MIN_DISCHARGE_PRICE)
            )
            fallback_min_energy_to_discharge = entry.options.get(
                CONF_MIN_ENERGY_TO_DISCHARGE, entry.data.get(CONF_MIN_ENERGY_TO_DISCHARGE, DEFAULT_MIN_ENERGY_TO_DISCHARGE)
            )

        if data is None:
            self._overrides = {}
            self.last_override_change = None
            self.min_bat_soc = fallback_min_bat_soc
            self.min_sell_price = fallback_min_sell_price
            self.min_discharge_price = fallback_min_discharge_price
            self.min_energy_to_discharge = fallback_min_energy_to_discharge
            self.boiler_heating_start_hour = 0.0
            self.boiler_heating_end_hour = 23.0
        else:
            self._overrides = data.get("overrides", {})
            self.last_override_change = data.get("last_override_change")
            self.min_bat_soc = data.get("min_bat_soc", fallback_min_bat_soc)
            self.min_sell_price = data.get("min_sell_price", fallback_min_sell_price)
            try:
                self.min_discharge_price = float(data.get("min_discharge_price", fallback_min_discharge_price))
            except (ValueError, TypeError):
                self.min_discharge_price = fallback_min_discharge_price
            self.min_energy_to_discharge = data.get("min_energy_to_discharge", fallback_min_energy_to_discharge)
            self.boiler_heating_start_hour = float(data.get("boiler_heating_start_hour", 0.0))
            self.boiler_heating_end_hour = float(data.get("boiler_heating_end_hour", 23.0))
        _LOGGER.debug(
            "EMS Schedule Storage loaded: %d dates with overrides for entry %s",
            len(self._overrides),
            self.entry_id,
        )

    async def async_save(self) -> None:
        """Save data to JSON storage."""
        await self._store.async_save({
            "overrides": self._overrides,
            "last_override_change": self.last_override_change,
            "min_bat_soc": self.min_bat_soc,
            "min_sell_price": self.min_sell_price,
            "min_discharge_price": self.min_discharge_price,
            "min_energy_to_discharge": self.min_energy_to_discharge,
            "boiler_heating_start_hour": self.boiler_heating_start_hour,
            "boiler_heating_end_hour": self.boiler_heating_end_hour,
        })

    def get_overrides(self) -> dict[str, dict[str, str]]:
        """Return all manual overrides."""
        import copy
        return copy.deepcopy(self._overrides)

    def get_hour_override(self, date_str: str, hour: int) -> str | None:
        """Get override action for a specific hour."""
        return self._overrides.get(date_str, {}).get(str(hour))

    async def async_set_override(self, date_str: str, hour: int, action: str) -> None:
        """Set a manual override for a specific hour."""
        if date_str not in self._overrides:
            self._overrides[date_str] = {}
        self._overrides[date_str][str(hour)] = action
        self.last_override_change = dt_util.now().isoformat()
        await self.async_save()
        _LOGGER.info("EMS Override set: %s hour %d -> %s", date_str, hour, action)

    async def async_clear_override(self, date_str: str, hour: int) -> None:
        """Clear a manual override for a specific hour."""
        if date_str in self._overrides and str(hour) in self._overrides[date_str]:
            del self._overrides[date_str][str(hour)]
            if not self._overrides[date_str]:
                del self._overrides[date_str]
            self.last_override_change = dt_util.now().isoformat()
            await self.async_save()
            _LOGGER.info("EMS Override cleared: %s hour %d", date_str, hour)

    async def async_clear_all_overrides(self) -> None:
        """Clear all manual overrides."""
        self._overrides = {}
        self.last_override_change = dt_util.now().isoformat()
        await self.async_save()
        _LOGGER.info("EMS All manual overrides cleared")

    def cleanup_old_dates(self, today_str: str) -> None:
        """Remove overrides older than today."""
        dates_to_remove = []
        for d_str in self._overrides:
            if d_str < today_str:
                dates_to_remove.append(d_str)
        for d_str in dates_to_remove:
            del self._overrides[d_str]
        if dates_to_remove:
            self.hass.async_create_task(self.async_save())
            _LOGGER.debug("EMS Cleaned up old overrides: %s", dates_to_remove)


# ---------------------------------------------------------------------------
# Calibration coefficient persistent storage
# ---------------------------------------------------------------------------

CALIBRATION_STORAGE_VERSION = 1
CALIBRATION_STORAGE_KEY = "ems_calibration_{entry_id}"

from .const import STANDBY_LOSSES_PRESETS

_CALIBRATION_DEFAULTS: dict = {
    "gas_only":       {"efficiency_c_per_m3": 0.0, "last_calibrated": None},
    "gas_with_pump":  {"efficiency_c_per_m3": 0.0, "last_calibrated": None},
    "elec_only":      {"efficiency_c_per_kwh": 0.0, "heater_power_kw": 2.5, "last_calibrated": None},
    "elec_with_pump": {"efficiency_c_per_kwh": 0.0, "heater_power_kw": 2.5, "last_calibrated": None},
    "standby_losses": {
        "gas":  {k: {"value": v, "updated_at": None} for k, v in STANDBY_LOSSES_PRESETS["gas"].items()},
        "elec": {k: {"value": v, "updated_at": None} for k, v in STANDBY_LOSSES_PRESETS["elec"].items()},
        "last_calibrated": None,
    },
}


class EmsCalibrationStore:
    """Persistent JSON storage for boiler calibration coefficients.

    Uses homeassistant.helpers.storage.Store so data is written immediately
    to disk on every update — immune to hot config-entry reloads that cause
    RestoreSensor to return stale values.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(
            hass,
            CALIBRATION_STORAGE_VERSION,
            CALIBRATION_STORAGE_KEY.format(entry_id=entry_id),
        )
        self._data: dict = {}

    async def async_load(self) -> None:
        """Load calibration coefficients from JSON file (or use defaults)."""
        raw = await self._store.async_load()
        import copy
        if raw is None:
            self._data = copy.deepcopy(_CALIBRATION_DEFAULTS)
            _LOGGER.debug("EMS CalibrationStore: no saved data found, using defaults.")
        else:
            # Merge stored values into defaults so new keys are always present
            self._data = copy.deepcopy(_CALIBRATION_DEFAULTS)
            for key in _CALIBRATION_DEFAULTS:
                if key in raw:
                    self._data[key].update(raw[key])
            _LOGGER.debug("EMS CalibrationStore loaded for entry %s.", self.entry_id)

    async def async_save(self) -> None:
        """Persist calibration coefficients to disk immediately."""
        await self._store.async_save(self._data)

    def get_all(self) -> dict:
        """Return a shallow copy of all stored calibration data."""
        import copy
        return copy.deepcopy(self._data)

    def update_phase(self, phase: str, data: dict) -> None:
        """Update coefficients for a specific phase in memory (call async_save to persist).

        For 'standby_losses', performs a nested merge into gas/elec sub-dicts so that
        a partial bracket update does not overwrite the remaining brackets.
        """
        if phase not in self._data:
            _LOGGER.warning("EmsCalibrationStore: unknown phase '%s', skipping.", phase)
            return

        if phase == "standby_losses":
            # Nested merge: only update the brackets that are explicitly passed
            for boiler in ("gas", "elec"):
                if boiler in data and isinstance(data[boiler], dict):
                    self._data[phase].setdefault(boiler, {})
                    self._data[phase][boiler].update(data[boiler])
            if "last_calibrated" in data:
                self._data[phase]["last_calibrated"] = data["last_calibrated"]
        else:
            self._data[phase].update(data)
