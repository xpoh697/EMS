"""The Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
from pathlib import Path
from aiohttp import web

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import frontend
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
    from .storage import EmsScheduleStorage
    storage = EmsScheduleStorage(hass, entry.entry_id)
    await storage.async_load(entry)

    # Store settings and storage in memory
    hass.data[DOMAIN][entry.entry_id] = {
        **entry.data,
        "storage": storage,
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

    # Register options update listener to reload when settings change
    entry.async_on_unload(entry.add_update_listener(async_update_options_listener))
    
    # Forward entry setups to platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "number"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "number"])
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


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the Lovelace card with a cache-busting version query string."""
    card_url = f"/api/{DOMAIN}/static/ems-scheduler-card.js?v={VERSION}"

    # Try to register as a Lovelace resource (Storage Mode)
    registered_as_resource = await _async_register_lovelace_resource(hass, card_url)
    if not registered_as_resource:
        # Fallback for YAML mode
        frontend.add_extra_js_url(hass, card_url)
        _LOGGER.debug("Registered card via extra_js_url fallback: %s", card_url)
    else:
        _LOGGER.debug("Registered card via Lovelace resource: %s", card_url)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Create or update the Lovelace resource entry for the card."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return False

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return False

    if not hasattr(resources, "async_create_item") or not hasattr(resources, "async_update_item"):
        return False

    existing = None
    try:
        for item in resources.async_items():
            if "ems-scheduler-card.js" in item.get("url", ""):
                existing = item
                break
    except Exception:
        return False

    try:
        if existing is not None:
            if existing.get("url") != url:
                await resources.async_update_item(existing["id"], {"res_type": "module", "url": url})
                _LOGGER.info("Updated Lovelace resource: %s", url)
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
        if filename != "ems-scheduler-card.js":
            return web.Response(status=404)

        file_path = self._www_path / filename
        if not file_path.exists():
            return web.Response(status=404)

        try:
            return web.FileResponse(file_path)
        except Exception:
            return web.Response(status=500)
