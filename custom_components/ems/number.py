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
        EmsMinBatSocNumber(entry.entry_id, entry.title, storage),
        EmsMinSellPriceNumber(entry.entry_id, entry.title, storage),
    ])


class EmsMinBatSocNumber(NumberEntity):
    """EMS Minimum Battery SOC number entity."""

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
        self._attr_name = "Minimum Battery SOC"
        self._attr_unique_id = f"{entry_id}_min_bat_soc"
        self.entity_id = "number.ems_min_bat_soc"

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
        """Return the current minimum battery SOC."""
        return self._storage.min_bat_soc

    async def async_set_native_value(self, value: float) -> None:
        """Update the minimum battery SOC value."""
        clamped_value = float(max(self.native_min_value, min(value, self.native_max_value)))
        self._storage.min_bat_soc = clamped_value
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
