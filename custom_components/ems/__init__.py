"""The Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
from pathlib import Path
from aiohttp import web
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN, CONF_DEBUG, VERSION
from .utils import setup_ems_logger

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EMS from a config entry."""
    # Set up custom rotating file logger for the integration
    await setup_ems_logger(hass)

    hass.data.setdefault(DOMAIN, {})
    
    # Initialize schedule storage manager
    from .storage import EmsScheduleStorage, EmsCalibrationStore
    storage = EmsScheduleStorage(hass, entry.entry_id)
    await storage.async_load(entry)

    # Initialize calibration storage manager
    calibration_store = EmsCalibrationStore(hass, entry.entry_id)
    await calibration_store.async_load()

    # Initialize Boiler Controller
    from .boiler_controller import BoilerController
    boiler_config = entry.options if entry.options else entry.data
    boiler_controller = BoilerController(hass, boiler_config)
    await boiler_controller.async_setup()

    # Store settings and storage in memory.
    # entry.options (Options Flow) overwrites entry.data for same keys —
    # this is intentional: boiler entity IDs live in options, not data.
    hass.data[DOMAIN][entry.entry_id] = {
        **entry.data,
        **entry.options,          # <-- FIX: merge options so WS API finds entity IDs
        "storage": storage,
        "calibration_store": calibration_store,
        "boiler_controller": boiler_controller,
    }
    
    # Cache debug flag for fast utility access
    debug_enabled = entry.options.get(CONF_DEBUG, entry.data.get(CONF_DEBUG, False))
    hass.data[DOMAIN]["debug"] = debug_enabled

    # Register the static HTTP view immediately to prevent 404 errors during early boot
    www_path = Path(__file__).parent / "www"
    hass.http.register_view(CardStaticView(www_path))
    
    # Defer Lovelace card database resource registration to prevent startup deadlocks
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    
    if hass.state == CoreState.running:
        hass.async_create_task(_async_register_card(hass))
    else:
        async def _register_card_after_start(event):
            await _async_register_card(hass)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_card_after_start)

    # Register services
    from .services import async_setup_services
    await async_setup_services(hass, entry)

    # Register WebSocket API (once per HA start, not per entry)
    if not hass.data[DOMAIN].get("_ws_registered"):
        websocket_api.async_register_command(hass, ws_get_boiler_config)
        hass.data[DOMAIN]["_ws_registered"] = True

    # Register options update listener to reload when settings change
    entry.async_on_unload(entry.add_update_listener(async_update_options_listener))
    
    # Forward entry setups to platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "number", "select"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "number", "select"])
    if unload_ok:
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN].pop(entry.entry_id)
        
        # Unload services if no entries remain
        from .services import async_unload_services
        async_unload_services(hass)
        
    return unload_ok

async def async_update_options_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update and reload the integration."""
    _LOGGER.debug("Options updated, reloading integration")
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# WebSocket API
# ---------------------------------------------------------------------------

@websocket_api.websocket_command(
    {
        vol.Required("type"): "ems/get_boiler_config",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_boiler_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return boiler entity_ids so the JS card does not need manual YAML config."""
    entry_id = msg.get("entry_id")

    if not entry_id:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            connection.send_error(msg["id"], "not_found", "No EMS integration configured")
            return
        entry_id = entries[0].entry_id

    ems_data = hass.data.get(DOMAIN, {})
    if entry_id not in ems_data:
        connection.send_error(msg["id"], "not_ready", "EMS integration is still loading, try again in a moment")
        return

    config = ems_data[entry_id]

    # Look up actual entity_id for the EMS load_consumption sensor via entity registry
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    consumption_entity = registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_load_consumption")

    connection.send_result(msg["id"], {
        "entry_id": entry_id,
        "gas_climate":  config.get("gas_boiler_climate"),
        "elec_heater":  config.get("elec_boiler_heater"),
        "elec_power":   config.get("elec_boiler_power"),
        "elec_temp":    config.get("elec_boiler_temp"),
        "pump":         config.get("circulation_pump"),
        "valve":        config.get("bypass_valve"),
        "hw_pump":      config.get("hw_circulation_pump"),
        "hw_return_temp": config.get("hw_circulation_return_temp"),
        "mode_select":  f"select.ems_boiler_mode",
        "consumption_entity": consumption_entity or "sensor.load_consumption_2",
        "heating_start_hour": "number.ems_boiler_heating_start_hour",
        "heating_end_hour": "number.ems_boiler_heating_end_hour",
    })


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the Lovelace cards with a cache-busting version query string."""
    cards = ["ems-scheduler-card.js", "boiler-card.js"]
    for card_name in cards:
        card_url = f"/api/{DOMAIN}/static/{card_name}?v={VERSION}"
        # Try to register as a Lovelace resource (Storage Mode)
        registered_as_resource = await _async_register_lovelace_resource(hass, card_url)
        if not registered_as_resource:
            # Fallback for YAML mode
            frontend.add_extra_js_url(hass, card_url)
            _LOGGER.debug("Registered card via extra_js_url fallback: %s", card_url)
        else:
            _LOGGER.debug("Registered card via Lovelace resource: %s", card_url)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Create or update the Lovelace resource entry for the card, cleaning up duplicates."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return False

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return False

    if not hasattr(resources, "async_create_item") or not hasattr(resources, "async_update_item"):
        return False

    base_url = url.split("?")[0]
    existing_items = []
    try:
        for item in resources.async_items():
            existing_url = item.get("url") or ""
            existing_base = existing_url.split("?")[0]
            if existing_base == base_url:
                existing_items.append(item)
    except Exception:
        return False

    try:
        if existing_items:
            # Update the first resource
            first_item = existing_items[0]
            if first_item.get("url") != url:
                await resources.async_update_item(first_item["id"], {"res_type": "module", "url": url})
                _LOGGER.info("Updated Lovelace resource: %s", url)
            
            # Safely delete duplicates in separate step
            if len(existing_items) > 1 and hasattr(resources, "async_delete_item"):
                for duplicate in existing_items[1:]:
                    await resources.async_delete_item(duplicate["id"])
                    _LOGGER.info("Deleted duplicate Lovelace resource: %s", duplicate.get("url"))
        else:
            await resources.async_create_item({"res_type": "module", "url": url})
            _LOGGER.info("Created Lovelace resource: %s", url)
    except Exception as err:
        _LOGGER.warning("Failed to register Lovelace resource: %s", err)
        return False

    return True


class CardStaticView(HomeAssistantView):
    """View to serve static card files with CORS."""
    url = f"/api/{DOMAIN}/static/{{filename}}"
    name = f"api:{DOMAIN}:static"
    requires_auth = False
    cors_allowed = True

    def __init__(self, www_path: Path) -> None:
        self._www_path = www_path

    async def get(self, request, filename: str):
        """Handle GET request for static files."""
        if filename not in ["ems-scheduler-card.js", "boiler-card.js"]:
            return web.Response(status=404)

        file_path = self._www_path / filename
        if not file_path.exists():
            return web.Response(status=404)

        try:
            return web.FileResponse(file_path)
        except Exception:
            return web.Response(status=500)
