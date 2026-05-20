"""Storage helper for EMS schedules and manual overrides."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

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

    async def async_load(self) -> None:
        """Load data from JSON storage."""
        data = await self._store.async_load()
        if data is None:
            self._overrides = {}
        else:
            self._overrides = data.get("overrides", {})
        _LOGGER.debug(
            "EMS Schedule Storage loaded: %d dates with overrides for entry %s",
            len(self._overrides),
            self.entry_id,
        )

    async def async_save(self) -> None:
        """Save data to JSON storage."""
        await self._store.async_save({
            "overrides": self._overrides,
        })

    def get_overrides(self) -> dict[str, dict[str, str]]:
        """Return all manual overrides."""
        return self._overrides

    def get_hour_override(self, date_str: str, hour: int) -> str | None:
        """Get override action for a specific hour."""
        return self._overrides.get(date_str, {}).get(str(hour))

    async def async_set_override(self, date_str: str, hour: int, action: str) -> None:
        """Set a manual override for a specific hour."""
        if date_str not in self._overrides:
            self._overrides[date_str] = {}
        self._overrides[date_str][str(hour)] = action
        await self.async_save()
        _LOGGER.info("EMS Override set: %s hour %d -> %s", date_str, hour, action)

    async def async_clear_override(self, date_str: str, hour: int) -> None:
        """Clear a manual override for a specific hour."""
        if date_str in self._overrides and str(hour) in self._overrides[date_str]:
            del self._overrides[date_str][str(hour)]
            if not self._overrides[date_str]:
                del self._overrides[date_str]
            await self.async_save()
            _LOGGER.info("EMS Override cleared: %s hour %d", date_str, hour)

    async def async_clear_all_overrides(self) -> None:
        """Clear all manual overrides."""
        self._overrides = {}
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
