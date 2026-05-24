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
    CONF_TOTAL_GRID_EXPORT,
    CONF_TOTAL_GRID_IMPORT,
    CONF_CURRENT_HOUSE_CONSUMPTION,
    CONF_INVERTER_MODES_LIST,
    CONF_CURRENT_PV_GENERATION,
    CONF_PV_GENERATION_TODAY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    CONF_DEBUG,
    CONF_CALIBRATION_TYPE,
    CONF_WATER_FLOW_SENSOR,
    CONF_PRICE_BUY_SENSOR,
    CONF_PRICE_SELL_SENSOR,
    CONF_SYSTEM_COST,
    CONF_MIN_SELL_PRICE,
    CONF_BAT_PRICE,
    CONF_BAT_CYCLES,
    CONF_BAT_CAPACITY_ENTITY,
    CONF_BAT_MAX_POWER,
    CONF_BAT_CUR_POWER_ENTITY,
    CONF_BAT_SOC_ENTITY,
    CONF_BAT_VOLTAGE,
    CONF_MIN_BAT_SOC,
    CONF_BAT_SOC_EMERGENCY,
    CONF_HW_CIRCULATION_PUMP,
    CONF_HW_CIRCULATION_RETURN_TEMP,
    CONF_THERMOSTAT_SET_TEMP,
    CONF_ELEC_BOILER_MAX_TEMP,
    CONF_GAS_BOILER_MAX_TEMP,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_DEBUG,
    DEFAULT_SYSTEM_COST,
    DEFAULT_MIN_SELL_PRICE,
    DEFAULT_BAT_PRICE,
    DEFAULT_BAT_CYCLES,
    DEFAULT_BAT_MAX_POWER,
    DEFAULT_MIN_BAT_SOC,
    DEFAULT_BAT_SOC_EMERGENCY,
    DEFAULT_THERMOSTAT_SET_TEMP,
    DEFAULT_ELEC_BOILER_MAX_TEMP,
    DEFAULT_GAS_BOILER_MAX_TEMP,
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
            if category == "financial":
                return await self.async_step_financial()
            if category == "battery_optimization":
                return await self.async_step_battery_optimization()
            if category == "boiler":
                return await self.async_step_boiler()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("category", default="basic_settings"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "basic_settings", "label": "Basic settings"},
                            {"value": "pv_forecast", "label": "PV Forecast"},
                            {"value": "financial", "label": "Financial"},
                            {"value": "battery_optimization", "label": "Battery optimization"},
                            {"value": "boiler", "label": "Boiler Configuration"},
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
        # Required: CONF_TOTAL_LOAD_CONSUMPTION
        val_total = get_value(CONF_TOTAL_LOAD_CONSUMPTION)
        if val_total:
            schema_dict[vol.Required(CONF_TOTAL_LOAD_CONSUMPTION, default=val_total)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Required(CONF_TOTAL_LOAD_CONSUMPTION)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # Required: CONF_TOTAL_GRID_EXPORT
        val_grid_export = get_value(CONF_TOTAL_GRID_EXPORT)
        if val_grid_export:
            schema_dict[vol.Required(CONF_TOTAL_GRID_EXPORT, default=val_grid_export)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Required(CONF_TOTAL_GRID_EXPORT)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # Required: CONF_TOTAL_GRID_IMPORT
        val_grid_import = get_value(CONF_TOTAL_GRID_IMPORT)
        if val_grid_import:
            schema_dict[vol.Required(CONF_TOTAL_GRID_IMPORT, default=val_grid_import)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Required(CONF_TOTAL_GRID_IMPORT)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # Optional: CONF_CURRENT_HOUSE_CONSUMPTION
        val_curr = get_value(CONF_CURRENT_HOUSE_CONSUMPTION)
        if val_curr:
            schema_dict[vol.Optional(CONF_CURRENT_HOUSE_CONSUMPTION, default=val_curr)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_CURRENT_HOUSE_CONSUMPTION)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 2. Inverter Modes List
        val_inv = get_value(CONF_INVERTER_MODES_LIST)
        if val_inv:
            schema_dict[vol.Optional(CONF_INVERTER_MODES_LIST, default=val_inv)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "input_select"])
            )
        else:
            schema_dict[vol.Optional(CONF_INVERTER_MODES_LIST)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "input_select"])
            )

        # 3. Calculation & Fallback Parameters
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

        # 4. System Settings
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
        # Optional: CONF_CURRENT_PV_GENERATION, CONF_PV_GENERATION_TODAY
        for key in [CONF_CURRENT_PV_GENERATION, CONF_PV_GENERATION_TODAY]:
            val = get_value(key)
            if val:
                schema_dict[vol.Optional(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            else:
                schema_dict[vol.Optional(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        # Required: CONF_PV_FORECAST_TODAY, CONF_PV_FORECAST_TOMORROW
        for key in [CONF_PV_FORECAST_TODAY, CONF_PV_FORECAST_TOMORROW]:
            val = get_value(key)
            if val:
                schema_dict[vol.Required(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            else:
                schema_dict[vol.Required(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        return self.async_show_form(
            step_id="pv_forecast",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_financial(self, user_input=None):
        """Handle the Financial settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        system_cost = self._user_input.get(CONF_SYSTEM_COST, DEFAULT_SYSTEM_COST)

        schema_dict = {}

        # Price Buy & Sell Sensors - Required
        for key in [CONF_PRICE_BUY_SENSOR, CONF_PRICE_SELL_SENSOR]:
            val = get_value(key)
            if val:
                schema_dict[vol.Required(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            else:
                schema_dict[vol.Required(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        schema_dict[vol.Required(CONF_SYSTEM_COST, default=system_cost)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        return self.async_show_form(
            step_id="financial",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_battery_optimization(self, user_input=None):
        """Handle the Battery optimization settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        bat_price = self._user_input.get(CONF_BAT_PRICE, DEFAULT_BAT_PRICE)
        bat_cycles = self._user_input.get(CONF_BAT_CYCLES, DEFAULT_BAT_CYCLES)
        bat_max_power = self._user_input.get(CONF_BAT_MAX_POWER, DEFAULT_BAT_MAX_POWER)
        bat_soc_emergency = self._user_input.get(CONF_BAT_SOC_EMERGENCY, DEFAULT_BAT_SOC_EMERGENCY)

        schema_dict = {}

        # 1. Numbers
        schema_dict[vol.Required(CONF_BAT_PRICE, default=bat_price)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        schema_dict[vol.Required(CONF_BAT_CYCLES, default=bat_cycles)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                step=1,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        # 2. Bat Capacity (entity) - Required
        val_cap = get_value(CONF_BAT_CAPACITY_ENTITY)
        if val_cap:
            schema_dict[vol.Required(CONF_BAT_CAPACITY_ENTITY, default=val_cap)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Required(CONF_BAT_CAPACITY_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        schema_dict[vol.Required(CONF_BAT_MAX_POWER, default=bat_max_power)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                step=1.0,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        # 3. Bat SOC (entity) - Required
        val_soc = get_value(CONF_BAT_SOC_ENTITY)
        if val_soc:
            schema_dict[vol.Required(CONF_BAT_SOC_ENTITY, default=val_soc)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Required(CONF_BAT_SOC_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 4. Bat Current Power (entity) - Optional
        val_cur_pow = get_value(CONF_BAT_CUR_POWER_ENTITY)
        if val_cur_pow:
            schema_dict[vol.Optional(CONF_BAT_CUR_POWER_ENTITY, default=val_cur_pow)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_BAT_CUR_POWER_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 5. Bat Voltage (entity) - Optional
        val_voltage = get_value(CONF_BAT_VOLTAGE)
        if val_voltage:
            schema_dict[vol.Optional(CONF_BAT_VOLTAGE, default=val_voltage)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_BAT_VOLTAGE)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 6. Bat SOC Emergency level
        schema_dict[vol.Required(CONF_BAT_SOC_EMERGENCY, default=bat_soc_emergency)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=100.0,
                step=0.1,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        return self.async_show_form(
            step_id="battery_optimization",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_boiler(self, user_input=None):
        """Handle the Boiler settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        schema_dict = {}

        keys_domains = {
            "gas_boiler_climate": "climate",
            "gas_boiler_meter": "sensor",
            "elec_boiler_heater": "switch",
            "elec_boiler_power": "sensor",
            "elec_boiler_energy": "sensor",
            "elec_boiler_temp": "sensor",
            "circulation_pump": "switch",
            "bypass_valve": ["switch", "input_boolean"],
            CONF_HW_CIRCULATION_PUMP: "switch",
            CONF_HW_CIRCULATION_RETURN_TEMP: "sensor"
        }

        for key, domain in keys_domains.items():
            val = get_value(key)
            if val:
                schema_dict[vol.Required(key, default=val)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=domain)
                )
            else:
                schema_dict[vol.Required(key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=domain)
                )

        # Безопасное получение числовых значений
        gas_cap = self._user_input.get("gas_boiler_capacity", 100)
        elec_cap = self._user_input.get("elec_boiler_capacity", 100)
        gas_cost = self._user_input.get("gas_cost_m3", 0.0)
        thermostat_temp = self._user_input.get(CONF_THERMOSTAT_SET_TEMP, DEFAULT_THERMOSTAT_SET_TEMP)
        elec_max_temp = self._user_input.get(CONF_ELEC_BOILER_MAX_TEMP, DEFAULT_ELEC_BOILER_MAX_TEMP)
        gas_max_temp = self._user_input.get(CONF_GAS_BOILER_MAX_TEMP, DEFAULT_GAS_BOILER_MAX_TEMP)

        schema_dict[vol.Required(CONF_THERMOSTAT_SET_TEMP, default=thermostat_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=60.0, step=0.5, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required(CONF_ELEC_BOILER_MAX_TEMP, default=elec_max_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=75.0, step=1.0, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required(CONF_GAS_BOILER_MAX_TEMP, default=gas_max_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=70.0, step=1.0, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required("gas_boiler_capacity", default=gas_cap)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, step=1, mode=selector.NumberSelectorMode.BOX)
        )
        schema_dict[vol.Required("elec_boiler_capacity", default=elec_cap)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, step=1, mode=selector.NumberSelectorMode.BOX)
        )
        schema_dict[vol.Required("gas_cost_m3", default=gas_cost)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, step=0.01, mode=selector.NumberSelectorMode.BOX)
        )

        # Select for calibration mode: CONF_CALIBRATION_TYPE
        cal_type = self._user_input.get(CONF_CALIBRATION_TYPE, "manual")
        schema_dict[vol.Required(CONF_CALIBRATION_TYPE, default=cal_type)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": "manual", "label": "Manual"},
                    {"value": "auto", "label": "Auto"},
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="calibration_type"
            )
        )

        # Optional: CONF_WATER_FLOW_SENSOR
        val_flow = get_value(CONF_WATER_FLOW_SENSOR)
        if val_flow:
            schema_dict[vol.Optional(CONF_WATER_FLOW_SENSOR, default=val_flow)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )
        else:
            schema_dict[vol.Optional(CONF_WATER_FLOW_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )

        return self.async_show_form(
            step_id="boiler",
            data_schema=vol.Schema(schema_dict)
        )
