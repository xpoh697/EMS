"""Config flow for Energy Management System (EMS) integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_TOTAL_LOAD_CONSUMPTION,
    CONF_CURRENT_HOUSE_CONSUMPTION,
    CONF_CURRENT_PV_GENERATION,
    CONF_PV_GENERATION_TODAY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    CONF_DEBUG,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_DEBUG,
)

_LOGGER = logging.getLogger(__name__)

class EmsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EMS."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return EmsOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle the initial config flow setup step."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get("name", "Energy Management System"),
                data=user_input
            )

        schema = vol.Schema({
            vol.Required("name", default="Energy Management System"): cv.string,
        })
        return self.async_show_form(step_id="user", data_schema=schema)


class EmsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow with category menu."""

    async def async_step_init(self, user_input=None):
        """Manage the options via a category selection form."""
        self._user_input = dict(self.config_entry.data)
        if self.config_entry.options:
            self._user_input.update(self.config_entry.options)

        if user_input is not None:
            category = user_input.get("category")
            if category == "basic_settings":
                return await self.async_step_basic_settings()
            if category == "pv_forecast":
                return await self.async_step_pv_forecast()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("category", default="basic_settings"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "basic_settings", "label": "Basic settings"},
                            {"value": "pv_forecast", "label": "PV Forecast"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            })
        )

    async def async_step_basic_settings(self, user_input=None):
        """Handle the Basic settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        stats_days = self._user_input.get(CONF_STATISTICS_DAYS, DEFAULT_STATISTICS_DAYS)
        fallback_cons = self._user_input.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION)
        debug_val = self._user_input.get(CONF_DEBUG, DEFAULT_DEBUG)

        schema_dict = {}

        # 1. House Consumption Sensors
        for key in [CONF_TOTAL_LOAD_CONSUMPTION, CONF_CURRENT_HOUSE_CONSUMPTION]:
            val = get_value(key)
            if val:
                schema_dict[vol.Optional(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            else:
                schema_dict[vol.Optional(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        # 2. Calculation & Fallback Parameters
        schema_dict[vol.Required(CONF_STATISTICS_DAYS, default=stats_days)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=365,
                step=1,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        schema_dict[vol.Required(CONF_FALLBACK_CONSUMPTION, default=fallback_cons)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=100.0,
                step=0.1,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        # 3. System Settings
        schema_dict[vol.Required(CONF_DEBUG, default=debug_val)] = selector.BooleanSelector()

        return self.async_show_form(
            step_id="basic_settings",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_pv_forecast(self, user_input=None):
        """Handle the PV Forecast settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        schema_dict = {}

        # PV Generation & Forecast Sensors
        for key in [
            CONF_CURRENT_PV_GENERATION,
            CONF_PV_GENERATION_TODAY,
            CONF_PV_FORECAST_TODAY,
            CONF_PV_FORECAST_TOMORROW,
        ]:
            val = get_value(key)
            if val:
                schema_dict[vol.Optional(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            else:
                schema_dict[vol.Optional(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        return self.async_show_form(
            step_id="pv_forecast",
            data_schema=vol.Schema(schema_dict)
        )
