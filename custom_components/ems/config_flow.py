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
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
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

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("category", default="basic_settings"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "basic_settings", "label": "Basic settings"},
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

        # Build schema dynamically to avoid voluptuous validation issues with missing keys
        schema_dict = {}
        for key in [
            CONF_TOTAL_LOAD_CONSUMPTION,
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
            step_id="basic_settings",
            data_schema=vol.Schema(schema_dict)
        )
