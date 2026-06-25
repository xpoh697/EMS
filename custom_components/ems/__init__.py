"""The Energy Management System (EMS) integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
    boiler_controller.entry_id = entry.entry_id
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
        websocket_api.async_register_command(hass, ws_get_history_30_days)
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
        "boiler_auto_temp_limit": "number.ems_boiler_auto_temp_limit",
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ems/get_history_30_days",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_history_30_days(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return 30 days of hourly history statistics for unified chart."""
    _LOGGER.debug("ws_get_history_30_days: called with msg %s", msg)
    entry_id = msg.get("entry_id")
    if not entry_id:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            _LOGGER.error("ws_get_history_30_days: No EMS integration configured")
            connection.send_error(msg["id"], "not_found", "No EMS integration configured")
            return
        entry_id = entries[0].entry_id

    _LOGGER.debug("ws_get_history_30_days: resolved entry_id=%s", entry_id)
    ems_data = hass.data.get(DOMAIN, {})
    if entry_id not in ems_data:
        _LOGGER.error("ws_get_history_30_days: EMS integration is still loading")
        connection.send_error(msg["id"], "not_ready", "EMS integration is still loading")
        return

    config = ems_data[entry_id]

    from .const import (
        CONF_TOTAL_LOAD_CONSUMPTION,
        CONF_PV_GENERATION_TODAY,
        CONF_CURRENT_PV_GENERATION,
        CONF_TOTAL_GRID_IMPORT,
        CONF_TOTAL_GRID_EXPORT,
        CONF_BAT_SOC_ENTITY,
        CONF_PRICE_BUY_SENSOR,
        CONF_PRICE_SELL_SENSOR,
        CONF_BAT_CAPACITY_ENTITY,
    )

    load_entity = config.get(CONF_TOTAL_LOAD_CONSUMPTION)
    pv_entity = config.get(CONF_PV_GENERATION_TODAY) or config.get(CONF_CURRENT_PV_GENERATION)
    import_entity = config.get(CONF_TOTAL_GRID_IMPORT)
    export_entity = config.get(CONF_TOTAL_GRID_EXPORT)
    soc_entity = config.get(CONF_BAT_SOC_ENTITY)
    buy_entity = config.get(CONF_PRICE_BUY_SENSOR)
    sell_entity = config.get(CONF_PRICE_SELL_SENSOR)
    cap_entity = config.get(CONF_BAT_CAPACITY_ENTITY)

    _LOGGER.debug(
        "ws_get_history_30_days: entities resolved: load=%s, pv=%s, import=%s, export=%s, soc=%s, buy=%s, sell=%s, cap=%s",
        load_entity, pv_entity, import_entity, export_entity, soc_entity, buy_entity, sell_entity, cap_entity
    )

    capacity = 5.12
    if cap_entity:
        state = hass.states.get(cap_entity)
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                capacity = float(state.state)
                unit = state.attributes.get("unit_of_measurement")
                if unit == "Wh" or capacity > 100.0:
                    capacity /= 1000.0
            except (ValueError, TypeError):
                pass
    _LOGGER.debug("ws_get_history_30_days: battery capacity=%s", capacity)

    if "recorder" not in hass.config.components:
        _LOGGER.error("ws_get_history_30_days: recorder not loaded")
        connection.send_error(msg["id"], "recorder_not_loaded", "Recorder is not loaded")
        return

    from homeassistant.components.recorder.statistics import statistics_during_period
    import homeassistant.util.dt as dt_util

    now = dt_util.now()
    start_time = (now - timedelta(days=31)).replace(hour=0, minute=0, second=0, microsecond=0)

    entity_ids = {load_entity, pv_entity, import_entity, export_entity, soc_entity, buy_entity, sell_entity}
    entity_ids = {e for e in entity_ids if e}

    _LOGGER.debug("ws_get_history_30_days: querying DB stats for entities=%s from %s to %s", entity_ids, start_time, now)

    try:
        stats = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            start_time,
            now,
            entity_ids,
            "hour",
            None,
            {"sum", "mean"},
        )
        _LOGGER.debug("ws_get_history_30_days: DB query complete, returned %s entities", len(stats) if stats else 0)
    except Exception as err:
        _LOGGER.exception("ws_get_history_30_days: error querying stats DB")
        connection.send_error(msg["id"], "db_error", f"Error fetching statistics: {err}")
        return

    def process_stats(stats_data, tzinfo, buy_prices_fallback, sell_prices_fallback):
        _LOGGER.debug("ws_get_history_30_days: processing statistics data in executor thread")
        # Group statistics by entity ID
        data_by_entity = {}
        for entity_id in [load_entity, pv_entity, import_entity, export_entity, soc_entity, buy_entity, sell_entity]:
            if not entity_id:
                continue
            ent_stats = stats_data.get(entity_id, [])
            ent_stats = sorted(ent_stats, key=lambda x: x.get("start") or 0)
            data_by_entity[entity_id] = ent_stats
            _LOGGER.debug("ws_get_history_30_days: entity %s has %d stats records", entity_id, len(ent_stats))

        def parse_start(start):
            if isinstance(start, (int, float)):
                dt = datetime.fromtimestamp(start, tz=tzinfo)
            elif isinstance(start, datetime):
                dt = dt_util.as_local(start)
            else:
                return None, None
            return dt.strftime("%Y-%m-%d"), dt.hour

        def get_hourly_deltas(ent_stats):
            deltas = {}
            for i in range(1, len(ent_stats)):
                prev = ent_stats[i-1]
                curr = ent_stats[i]
                prev_sum = prev.get("sum")
                curr_sum = curr.get("sum")
                if prev_sum is not None and curr_sum is not None:
                    delta = curr_sum - prev_sum
                    if delta < 0:
                        delta = 0.0
                    date_str, hour = parse_start(curr.get("start"))
                    if date_str:
                        deltas[(date_str, hour)] = round(delta, 3)
            return deltas

        def get_hourly_means(ent_stats):
            means = {}
            for item in ent_stats:
                val = item.get("mean")
                if val is None:
                    val = item.get("state")
                if val is not None:
                    date_str, hour = parse_start(item.get("start"))
                    if date_str:
                        means[(date_str, hour)] = round(float(val), 4)
            return means

        load_deltas = get_hourly_deltas(data_by_entity.get(load_entity, []))
        pv_deltas = get_hourly_deltas(data_by_entity.get(pv_entity, []))
        import_deltas = get_hourly_deltas(data_by_entity.get(import_entity, []))
        export_deltas = get_hourly_deltas(data_by_entity.get(export_entity, []))
        
        soc_means = get_hourly_means(data_by_entity.get(soc_entity, []))
        buy_means = get_hourly_means(data_by_entity.get(buy_entity, []))
        sell_means = get_hourly_means(data_by_entity.get(sell_entity, []))

        def populate_from_fallback(prices_list, target_means):
            if not isinstance(prices_list, list):
                return
            for item in prices_list:
                if isinstance(item, dict) and "start" in item and "price" in item:
                    try:
                        start_str = item["start"]
                        if "T" in start_str:
                            parts = start_str.split("T")
                            date_part = parts[0]
                            hour_part = int(parts[1].split(":")[0])
                            price_val = float(item["price"])
                            target_means[(date_part, hour_part)] = round(price_val, 4)
                    except (ValueError, TypeError, IndexError):
                        pass

        populate_from_fallback(buy_prices_fallback, buy_means)
        populate_from_fallback(sell_prices_fallback, sell_means)

        _LOGGER.debug(
            "ws_get_history_30_days: stats deltas/means computed: load=%d, pv=%d, import=%d, export=%d, soc=%d, buy=%d, sell=%d",
            len(load_deltas), len(pv_deltas), len(import_deltas), len(export_deltas), len(soc_means), len(buy_means), len(sell_means)
        )

        # Fill hourly grid for 31 days
        now_local = datetime.now(tz=tzinfo)
        start_dt = (now_local - timedelta(days=31)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        result_days = {}
        last_soc = 50.0
        last_buy = 0.0
        last_sell = 0.0
        
        current_dt = start_dt
        end_dt = now_local.replace(hour=23, minute=0, second=0, microsecond=0)
        while current_dt <= end_dt:
            date_str = current_dt.strftime("%Y-%m-%d")
            hour = current_dt.hour
            
            day_entry = result_days.setdefault(date_str, [None]*24)
            
            is_future = current_dt > now_local
            
            load_val = None if is_future else load_deltas.get((date_str, hour), 0.0)
            pv_val = None if is_future else pv_deltas.get((date_str, hour), 0.0)
            import_val = None if is_future else import_deltas.get((date_str, hour), 0.0)
            export_val = None if is_future else export_deltas.get((date_str, hour), 0.0)
            
            if is_future:
                soc_val = None
            else:
                soc_val = soc_means.get((date_str, hour))
                if soc_val is not None:
                    last_soc = soc_val
                else:
                    soc_val = last_soc
                
            buy_val = buy_means.get((date_str, hour))
            if buy_val is not None:
                last_buy = buy_val
            else:
                buy_val = last_buy
                
            sell_val = sell_means.get((date_str, hour))
            if sell_val is not None:
                last_sell = sell_val
            else:
                sell_val = last_sell
                
            day_entry[hour] = {
                "hour": hour,
                "load_kwh": load_val,
                "pv_kwh": pv_val,
                "import_kwh": import_val,
                "export_kwh": export_val,
                "bat_soc": soc_val,
                "price_buy": buy_val,
                "price_sell": sell_val,
            }
            
            current_dt += timedelta(hours=1)
            
        # Clean up partial hours
        valid_days = {}
        for d, hrs in result_days.items():
            if None not in hrs or d == now_local.strftime("%Y-%m-%d"):
                valid_days[d] = [h for h in hrs if h is not None]
        
        _LOGGER.debug("ws_get_history_30_days: returning %d days of data", len(valid_days))
        return valid_days

    # Extract fallback price attributes in the thread-safe async context
    buy_state = hass.states.get(buy_entity) if buy_entity else None
    sell_state = hass.states.get(sell_entity) if sell_entity else None

    buy_prices_fallback = []
    if buy_state:
        for attr in ("price_today", "price_tomorrow"):
            val = buy_state.attributes.get(attr)
            if isinstance(val, list):
                buy_prices_fallback.extend(val)

    sell_prices_fallback = []
    if sell_state:
        for attr in ("price_today", "price_tomorrow"):
            val = sell_state.attributes.get(attr)
            if isinstance(val, list):
                sell_prices_fallback.extend(val)

    processed = await hass.async_add_executor_job(
        process_stats, stats, now.tzinfo, buy_prices_fallback, sell_prices_fallback
    )
    connection.send_result(msg["id"], {"days": processed, "capacity": capacity})


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
