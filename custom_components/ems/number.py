"""Number platform for EMS integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EMS number entities from config entry."""
    storage = hass.data[DOMAIN][entry.entry_id]["storage"]
    async_add_entities([
        EmsMinBatSocEveningNumber(entry.entry_id, entry.title, storage),
        EmsMinBatSocMorningNumber(entry.entry_id, entry.title, storage),
        EmsMinSellPriceNumber(entry.entry_id, entry.title, storage),
        EmsMinDischargePriceNumber(entry.entry_id, entry.title, storage),
        EmsMinEnergyToDischargeNumber(entry.entry_id, entry.title, storage),
        EmsBoilerHeatingStartHourNumber(entry.entry_id, entry.title, storage),
        EmsBoilerHeatingEndHourNumber(entry.entry_id, entry.title, storage),
        EmsBoilerAutoTempLimitNumber(entry.entry_id, entry.title, storage),
        EmsMinArbitrageProfitNumber(entry.entry_id, entry.title, storage),
    ])


class EmsMinBatSocEveningNumber(NumberEntity):
    """EMS Minimum Battery SOC Evening number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-arrow-down-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Min bat SOC evening"
        self._attr_unique_id = f"{entry_id}_min_bat_soc_evening"
        self.entity_id = "number.ems_min_bat_soc_evening"

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
        """Return the current minimum battery SOC for evening."""
        return self._storage.min_bat_soc_evening

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum battery SOC evening value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_bat_soc_evening = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsMinBatSocMorningNumber(NumberEntity):
    """EMS Minimum Battery SOC Morning number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-arrow-down-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Min bat SOC morning"
        self._attr_unique_id = f"{entry_id}_min_bat_soc_morning"
        self.entity_id = "number.ems_min_bat_soc_morning"

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
        """Return the current minimum battery SOC for morning."""
        return self._storage.min_bat_soc_morning

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum battery SOC morning value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_bat_soc_morning = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsMinSellPriceNumber(NumberEntity):
    """EMS Minimum Sell Price number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:currency-usd"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Minimum Sell Price"
        self._attr_unique_id = f"{entry_id}_min_sell_price"
        self.entity_id = "number.ems_min_sell_price"

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
        """Return the current minimum sell price."""
        return self._storage.min_sell_price

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum sell price value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_sell_price = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsMinEnergyToDischargeNumber(NumberEntity):
    """EMS Minimum Energy to Discharge number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-minus"
    _attr_native_unit_of_measurement = "kWh"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Min Energy to Discharge"
        self._attr_unique_id = f"{entry_id}_min_energy_to_discharge"
        self.entity_id = "number.ems_min_energy_to_discharge"

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
        """Return the current minimum energy to discharge."""
        return self._storage.min_energy_to_discharge

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum energy to discharge value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_energy_to_discharge = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsMinDischargePriceNumber(NumberEntity):
    """EMS Minimum Discharge Price number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:currency-usd"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Min Discharge Price"
        self._attr_unique_id = f"{entry_id}_min_discharge_price"
        self.entity_id = "number.ems_min_discharge_price"

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
        """Return the current minimum discharge price."""
        return self._storage.min_discharge_price

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum discharge price value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_discharge_price = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")
class EmsBoilerHeatingStartHourNumber(NumberEntity):
    """EMS Boiler Heating Start Hour number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 23.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:clock-start"
    _attr_native_unit_of_measurement = "h"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Boiler Heating Start Hour"
        self._attr_unique_id = f"{entry_id}_boiler_heating_start_hour"
        self.entity_id = "number.ems_boiler_heating_start_hour"

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
        """Return the current start hour."""
        return getattr(self._storage, "boiler_heating_start_hour", 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Update the start hour value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.boiler_heating_start_hour = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsBoilerHeatingEndHourNumber(NumberEntity):
    """EMS Boiler Heating End Hour number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 23.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:clock-end"
    _attr_native_unit_of_measurement = "h"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Boiler Heating End Hour"
        self._attr_unique_id = f"{entry_id}_boiler_heating_end_hour"
        self.entity_id = "number.ems_boiler_heating_end_hour"

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
        """Return the current end hour."""
        return getattr(self._storage, "boiler_heating_end_hour", 23.0)

    async def async_set_native_value(self, value: float) -> None:
        """Update the end hour value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.boiler_heating_end_hour = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")


class EmsBoilerAutoTempLimitNumber(NumberEntity):
    """EMS Boiler Auto Mode Temp Limit number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 40.0
    _attr_native_max_value = 85.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-alert"
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Boiler Auto Temp Limit"
        self._attr_unique_id = f"{entry_id}_boiler_auto_temp_limit"
        self.entity_id = "number.ems_boiler_auto_temp_limit"

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
        """Return the current auto temp limit."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        default_limit = 60.0
        if entry:
            default_limit = float(entry.options.get("elec_boiler_max_temp", entry.data.get("elec_boiler_max_temp", 60.0)))
        return getattr(self._storage, "boiler_auto_temp_limit", default_limit)

    async def async_set_native_value(self, value: float) -> None:
        """Update the auto temp limit value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.boiler_auto_temp_limit = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")

class EmsMinArbitrageProfitNumber(NumberEntity):
    """EMS Minimum Arbitrage Profit number entity."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:currency-usd"

    def __init__(self, entry_id: str, device_name: str, storage: Any) -> None:
        """Initialize the number entity."""
        self._entry_id = entry_id
        self._device_name = device_name
        self._storage = storage
        self._attr_name = "Min Arbitrage Profit"
        self._attr_unique_id = f"{entry_id}_min_arbitrage_profit"
        self.entity_id = "number.ems_min_arbitrage_profit"

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
        """Return the current minimum arbitrage profit."""
        return getattr(self._storage, "min_arbitrage_profit", 0.0)

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum arbitrage profit value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_arbitrage_profit = clamped_value
        await self._storage.async_save()
        self.async_write_ha_state()
        # Fire event to trigger immediate DP recalculation
        self.hass.bus.async_fire("ems_schedule_updated")

