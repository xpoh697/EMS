from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, VERSION

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the select platform for EMS."""
    async_add_entities([EmsBoilerModeSelect(hass, entry)])

class EmsBoilerModeSelect(SelectEntity, RestoreEntity):
    """Representation of an EMS Boiler Mode Select entity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the select entity."""
        self.hass = hass
        self._entry = entry
        
        # Name and ID
        title = entry.title if entry.title else "EMS"
        self._attr_name = f"{title} Boiler Mode"
        self._attr_unique_id = f"{entry.entry_id}_boiler_mode"
        
        self._attr_options = ["Auto", "Manual"]
        self._attr_current_option = "Auto"
        
        # Consistent entity_id formulation
        self.entity_id = f"select.ems_boiler_mode"

        # Register under the main EMS device (like sensors do)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=title,
            manufacturer="Energy Trader System",
            model="EMS Controller",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        
        # Restore previous state
        state = await self.async_get_last_state()
        if state and state.state in self._attr_options:
            self._attr_current_option = state.state
            
        # Push state to controller
        controller = self.hass.data[DOMAIN][self._entry.entry_id].get("boiler_controller")
        if controller:
            controller.current_mode = self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        self._attr_current_option = option
        self.async_write_ha_state()
        
        # Instantly push to controller
        controller = self.hass.data[DOMAIN][self._entry.entry_id].get("boiler_controller")
        if controller:
            controller.current_mode = option
