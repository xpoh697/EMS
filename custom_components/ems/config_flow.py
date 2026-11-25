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
    CONF_GRID_SENSOR,
    CONF_CURRENT_PV_GENERATION,
    CONF_PV_GENERATION_TODAY,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_STATISTICS_DAYS,
    CONF_FALLBACK_CONSUMPTION,
    CONF_DEBUG,
    CONF_VACATION_MODE_ENTITY,
    CONF_CALIBRATION_TYPE,
    CONF_WATER_FLOW_SENSOR,
    CONF_TOTAL_WATER_METER_SENSOR,
    CONF_PEOPLE_HOME_SENSOR,
    CONF_PRICE_BUY_SENSOR,
    CONF_PRICE_SELL_SENSOR,
    CONF_PRICE_BUY_MODE,
    CONF_PRICE_SELL_MODE,
    CONF_PRICE_BUY_FIXED,
    CONF_PRICE_SELL_FIXED,
    PRICE_MODE_SENSOR,
    PRICE_MODE_FIXED,
    DEFAULT_PRICE_MODE,
    DEFAULT_PRICE_BUY_FIXED,
    DEFAULT_PRICE_SELL_FIXED,
    CONF_SYSTEM_COST,
    CONF_MIN_SELL_PRICE,
    CONF_PRADCAST_TODAY_SENSOR,
    CONF_PRADCAST_TOMORROW_SENSOR,
    CONF_DISTRIBUTION_FEE_FALLBACK,
    DEFAULT_DISTRIBUTION_FEE_FALLBACK,
    CONF_BAT_PRICE,
    CONF_BAT_CYCLES,
    CONF_BAT_CAPACITY_ENTITY,
    CONF_BAT_CAPACITY_FALLBACK,
    CONF_BAT_MAX_CHARGE_POWER,
    CONF_BAT_MAX_DISCHARGE_POWER,
    CONF_MAX_DAILY_DISCHARGE_KWH,
    CONF_LIMIT_EXPORT_TO_MONTHLY_PV,
    CONF_PV_TOTAL_PRODUCTION_SENSOR,
    CONF_MONTHLY_EXPORT_SENSOR,
    CONF_MONTHLY_EXPORT_SAFETY_MARGIN_KWH,
    DEFAULT_BAT_MAX_CHARGE_POWER,
    DEFAULT_BAT_MAX_DISCHARGE_POWER,
    DEFAULT_MAX_DAILY_DISCHARGE_KWH,
    DEFAULT_LIMIT_EXPORT_TO_MONTHLY_PV,
    DEFAULT_MONTHLY_EXPORT_SAFETY_MARGIN_KWH,
    CONF_BAT_CUR_POWER_ENTITY,
    CONF_BAT_SOC_ENTITY,
    CONF_BAT_VOLTAGE,
    CONF_BAT_CHARGE_TODAY_ENTITY,
    CONF_BAT_DISCHARGE_TODAY_ENTITY,
    CONF_INVERTER_LOSSES_TODAY_ENTITY,
    CONF_MIN_BAT_SOC,
    CONF_BAT_SOC_EMERGENCY,
    CONF_HW_CIRCULATION_PUMP,
    CONF_HW_CIRCULATION_RETURN_TEMP,
    CONF_CWU_REQUEST_ENTITY,
    CONF_CWU_SETPOINT_ENTITY,
    CONF_THERMOSTAT_SET_TEMP,
    CONF_ELEC_BOILER_MAX_TEMP,
    CONF_GAS_BOILER_MAX_TEMP,
    CONF_BOILER_WARM_DIFF,
    DEFAULT_STATISTICS_DAYS,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_DEBUG,
    DEFAULT_SYSTEM_COST,
    DEFAULT_MIN_SELL_PRICE,
    DEFAULT_BAT_PRICE,
    DEFAULT_BAT_CYCLES,
    DEFAULT_BAT_CAPACITY_FALLBACK,
    DEFAULT_MIN_BAT_SOC,
    DEFAULT_BAT_SOC_EMERGENCY,
    DEFAULT_THERMOSTAT_SET_TEMP,
    DEFAULT_ELEC_BOILER_MAX_TEMP,
    DEFAULT_GAS_BOILER_MAX_TEMP,
    DEFAULT_BOILER_WARM_DIFF,
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
        vacation_entity_val = self._user_input.get(CONF_VACATION_MODE_ENTITY)

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

        # Inverter Grid Presence Sensor (Optional / Auto-detect)
        val_grid = get_value(CONF_GRID_SENSOR)
        if not val_grid and self.hass.states.get("binary_sensor.inverter_grid"):
            val_grid = "binary_sensor.inverter_grid"

        if val_grid:
            schema_dict[vol.Optional(CONF_GRID_SENSOR, default=val_grid)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_GRID_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
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

        # 5. Vacation Mode Entity (Optional)
        if vacation_entity_val:
            schema_dict[vol.Optional(CONF_VACATION_MODE_ENTITY, default=vacation_entity_val)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_boolean", "switch"])
            )
        else:
            schema_dict[vol.Optional(CONF_VACATION_MODE_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_boolean", "switch"])
            )

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
        errors: dict[str, str] = {}
        if user_input is not None:
            buy_mode_in = user_input.get(CONF_PRICE_BUY_MODE, PRICE_MODE_SENSOR)
            sell_mode_in = user_input.get(CONF_PRICE_SELL_MODE, PRICE_MODE_SENSOR)

            if buy_mode_in == PRICE_MODE_SENSOR and not user_input.get(CONF_PRICE_BUY_SENSOR):
                errors[CONF_PRICE_BUY_SENSOR] = "sensor_required"
            elif buy_mode_in == PRICE_MODE_FIXED and user_input.get(CONF_PRICE_BUY_FIXED) is None:
                errors[CONF_PRICE_BUY_FIXED] = "fixed_price_required"

            if sell_mode_in == PRICE_MODE_SENSOR and not user_input.get(CONF_PRICE_SELL_SENSOR):
                errors[CONF_PRICE_SELL_SENSOR] = "sensor_required"
            elif sell_mode_in == PRICE_MODE_FIXED and user_input.get(CONF_PRICE_SELL_FIXED) is None:
                errors[CONF_PRICE_SELL_FIXED] = "fixed_price_required"

            if not errors:
                self._user_input.update(user_input)
                for _opt_ent in (
                    CONF_PRICE_BUY_SENSOR,
                    CONF_PRICE_SELL_SENSOR,
                    CONF_PRADCAST_TODAY_SENSOR,
                    CONF_PRADCAST_TOMORROW_SENSOR,
                ):
                    if not user_input.get(_opt_ent):
                        self._user_input.pop(_opt_ent, None)
                for _fixed_key, _def in (
                    (CONF_PRICE_BUY_FIXED, DEFAULT_PRICE_BUY_FIXED),
                    (CONF_PRICE_SELL_FIXED, DEFAULT_PRICE_SELL_FIXED),
                ):
                    val = user_input.get(_fixed_key)
                    self._user_input[_fixed_key] = float(val) if val is not None else float(self._user_input.get(_fixed_key, _def))
                return self.async_create_entry(title="", data=self._user_input)

        source_data = user_input if user_input is not None else self._user_input

        def get_value(key):
            val = source_data.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        price_buy_mode = source_data.get(CONF_PRICE_BUY_MODE, DEFAULT_PRICE_MODE)
        if price_buy_mode not in (PRICE_MODE_SENSOR, PRICE_MODE_FIXED):
            price_buy_mode = DEFAULT_PRICE_MODE
        price_sell_mode = source_data.get(CONF_PRICE_SELL_MODE, DEFAULT_PRICE_MODE)
        if price_sell_mode not in (PRICE_MODE_SENSOR, PRICE_MODE_FIXED):
            price_sell_mode = DEFAULT_PRICE_MODE

        buy_fixed = source_data.get(CONF_PRICE_BUY_FIXED, DEFAULT_PRICE_BUY_FIXED)
        if buy_fixed is None:
            buy_fixed = DEFAULT_PRICE_BUY_FIXED
        sell_fixed = source_data.get(CONF_PRICE_SELL_FIXED, DEFAULT_PRICE_SELL_FIXED)
        if sell_fixed is None:
            sell_fixed = DEFAULT_PRICE_SELL_FIXED
        system_cost_raw = source_data.get(CONF_SYSTEM_COST, DEFAULT_SYSTEM_COST)
        system_cost = float(DEFAULT_SYSTEM_COST if system_cost_raw is None else system_cost_raw)

        def _make_mode_selector():
            return selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[PRICE_MODE_SENSOR, PRICE_MODE_FIXED],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="price_source_mode",
                )
            )

        def _make_price_number_selector():
            return selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-10.0,
                    max=50.0,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        def _add_optional_sensor(target_dict: dict, key: str, val: str | None) -> None:
            ent_sel = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            if val:
                target_dict[vol.Optional(key, description={"suggested_value": val})] = ent_sel
            else:
                target_dict[vol.Optional(key)] = ent_sel

        try:
            schema_dict = {}

            # Buy price: mode + optional sensor + fixed number
            schema_dict[vol.Required(CONF_PRICE_BUY_MODE, default=price_buy_mode)] = _make_mode_selector()
            _add_optional_sensor(schema_dict, CONF_PRICE_BUY_SENSOR, get_value(CONF_PRICE_BUY_SENSOR))
            schema_dict[vol.Required(CONF_PRICE_BUY_FIXED, default=float(buy_fixed))] = _make_price_number_selector()

            # Sell price: mode + optional sensor + fixed number
            schema_dict[vol.Required(CONF_PRICE_SELL_MODE, default=price_sell_mode)] = _make_mode_selector()
            _add_optional_sensor(schema_dict, CONF_PRICE_SELL_SENSOR, get_value(CONF_PRICE_SELL_SENSOR))
            schema_dict[vol.Required(CONF_PRICE_SELL_FIXED, default=float(sell_fixed))] = _make_price_number_selector()

            schema_dict[vol.Required(CONF_SYSTEM_COST, default=system_cost)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0,
                    step=0.01,
                    mode=selector.NumberSelectorMode.BOX
                )
            )

            # PradCast Price Sensors - Optional
            _add_optional_sensor(schema_dict, CONF_PRADCAST_TODAY_SENSOR, get_value(CONF_PRADCAST_TODAY_SENSOR))
            _add_optional_sensor(schema_dict, CONF_PRADCAST_TOMORROW_SENSOR, get_value(CONF_PRADCAST_TOMORROW_SENSOR))

            distrib_raw = source_data.get(CONF_DISTRIBUTION_FEE_FALLBACK, DEFAULT_DISTRIBUTION_FEE_FALLBACK)
            distrib_fallback = float(DEFAULT_DISTRIBUTION_FEE_FALLBACK if distrib_raw is None else distrib_raw)
            schema_dict[vol.Optional(CONF_DISTRIBUTION_FEE_FALLBACK, default=distrib_fallback)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0,
                    max=2.0,
                    step=0.01,
                    mode=selector.NumberSelectorMode.BOX
                )
            )

            if errors:
                return self.async_show_form(
                    step_id="financial",
                    data_schema=vol.Schema(schema_dict),
                    errors=errors,
                )
            return self.async_show_form(
                step_id="financial",
                data_schema=vol.Schema(schema_dict),
            )
        except Exception:
            _LOGGER.exception("Failed to build or show Financial options form")
            raise

    async def async_step_battery_optimization(self, user_input=None):
        """Handle the Battery optimization settings step."""
        if user_input is not None:
            self._user_input.update(user_input)
            for _opt_ent in (
                CONF_BAT_CAPACITY_ENTITY,
                CONF_PV_TOTAL_PRODUCTION_SENSOR,
                CONF_MONTHLY_EXPORT_SENSOR,
            ):
                if not user_input.get(_opt_ent):
                    self._user_input.pop(_opt_ent, None)
            return self.async_create_entry(title="", data=self._user_input)

        def get_value(key):
            val = self._user_input.get(key)
            if not val or val == "undefined":
                return None
            return str(val[0]) if isinstance(val, (list, tuple)) else str(val)

        bat_price = self._user_input.get(CONF_BAT_PRICE, DEFAULT_BAT_PRICE)
        bat_cycles = self._user_input.get(CONF_BAT_CYCLES, DEFAULT_BAT_CYCLES)
        bat_capacity_fallback = self._user_input.get(CONF_BAT_CAPACITY_FALLBACK, DEFAULT_BAT_CAPACITY_FALLBACK)
        bat_max_charge_power = self._user_input.get(CONF_BAT_MAX_CHARGE_POWER, DEFAULT_BAT_MAX_CHARGE_POWER)
        bat_max_discharge_power = self._user_input.get(CONF_BAT_MAX_DISCHARGE_POWER, DEFAULT_BAT_MAX_DISCHARGE_POWER)
        max_daily_discharge = float(self._user_input.get(CONF_MAX_DAILY_DISCHARGE_KWH, DEFAULT_MAX_DAILY_DISCHARGE_KWH))
        limit_export_to_monthly_pv = bool(self._user_input.get(CONF_LIMIT_EXPORT_TO_MONTHLY_PV, DEFAULT_LIMIT_EXPORT_TO_MONTHLY_PV))
        monthly_export_safety_margin = float(self._user_input.get(CONF_MONTHLY_EXPORT_SAFETY_MARGIN_KWH, DEFAULT_MONTHLY_EXPORT_SAFETY_MARGIN_KWH))
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

        # 2. Bat Capacity (entity) - Optional
        val_cap = get_value(CONF_BAT_CAPACITY_ENTITY)
        schema_dict[vol.Optional(CONF_BAT_CAPACITY_ENTITY, description={"suggested_value": val_cap} if val_cap else {})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )

        schema_dict[vol.Required(CONF_BAT_CAPACITY_FALLBACK, default=bat_capacity_fallback)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                step=0.1,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        schema_dict[vol.Required(CONF_BAT_MAX_CHARGE_POWER, default=bat_max_charge_power)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                step=1.0,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        schema_dict[vol.Required(CONF_BAT_MAX_DISCHARGE_POWER, default=bat_max_discharge_power)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                step=1.0,
                mode=selector.NumberSelectorMode.BOX
            )
        )

        schema_dict[vol.Required(CONF_MAX_DAILY_DISCHARGE_KWH, default=max_daily_discharge)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=100.0,
                step=0.5,
                unit_of_measurement="kWh",
                mode=selector.NumberSelectorMode.BOX
            )
        )

        # Monthly PV-balanced grid export limit
        schema_dict[vol.Required(CONF_LIMIT_EXPORT_TO_MONTHLY_PV, default=limit_export_to_monthly_pv)] = selector.BooleanSelector()

        val_pv_total = get_value(CONF_PV_TOTAL_PRODUCTION_SENSOR)
        if not val_pv_total and CONF_PV_TOTAL_PRODUCTION_SENSOR not in self._user_input and self.hass.states.get("sensor.inverter_total_production"):
            val_pv_total = "sensor.inverter_total_production"
        schema_dict[
            vol.Optional(
                CONF_PV_TOTAL_PRODUCTION_SENSOR,
                description={"suggested_value": val_pv_total} if val_pv_total else {},
            )
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        val_monthly_exp = get_value(CONF_MONTHLY_EXPORT_SENSOR)
        schema_dict[
            vol.Optional(
                CONF_MONTHLY_EXPORT_SENSOR,
                description={"suggested_value": val_monthly_exp} if val_monthly_exp else {},
            )
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        schema_dict[vol.Required(CONF_MONTHLY_EXPORT_SAFETY_MARGIN_KWH, default=monthly_export_safety_margin)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=100.0,
                step=0.5,
                unit_of_measurement="kWh",
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

        # 6. Bat Charge Today (entity) - Optional
        val_chg_today = get_value(CONF_BAT_CHARGE_TODAY_ENTITY)
        if not val_chg_today and self.hass.states.get("sensor.inverter_today_battery_charge"):
            val_chg_today = "sensor.inverter_today_battery_charge"
        if val_chg_today:
            schema_dict[vol.Optional(CONF_BAT_CHARGE_TODAY_ENTITY, default=val_chg_today)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_BAT_CHARGE_TODAY_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 7. Bat Discharge Today (entity) - Optional
        val_dis_today = get_value(CONF_BAT_DISCHARGE_TODAY_ENTITY)
        if not val_dis_today and self.hass.states.get("sensor.inverter_today_battery_discharge"):
            val_dis_today = "sensor.inverter_today_battery_discharge"
        if val_dis_today:
            schema_dict[vol.Optional(CONF_BAT_DISCHARGE_TODAY_ENTITY, default=val_dis_today)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_BAT_DISCHARGE_TODAY_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )

        # 8. Inverter Losses Today (entity) - Optional
        val_inv_losses = get_value(CONF_INVERTER_LOSSES_TODAY_ENTITY)
        if val_inv_losses:
            schema_dict[vol.Optional(CONF_INVERTER_LOSSES_TODAY_ENTITY, default=val_inv_losses)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_INVERTER_LOSSES_TODAY_ENTITY)] = selector.EntitySelector(
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

        # Optional CWU Request and Setpoint entities
        val_cwu_req = get_value(CONF_CWU_REQUEST_ENTITY)
        if val_cwu_req:
            schema_dict[vol.Optional(CONF_CWU_REQUEST_ENTITY, default=val_cwu_req)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
            )
        else:
            schema_dict[vol.Optional(CONF_CWU_REQUEST_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
            )

        val_cwu_sp = get_value(CONF_CWU_SETPOINT_ENTITY)
        if val_cwu_sp:
            schema_dict[vol.Optional(CONF_CWU_SETPOINT_ENTITY, default=val_cwu_sp)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
            )
        else:
            schema_dict[vol.Optional(CONF_CWU_SETPOINT_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
            )

        # Безопасное получение числовых значений
        gas_cap = self._user_input.get("gas_boiler_capacity", 100)
        elec_cap = self._user_input.get("elec_boiler_capacity", 100)
        gas_cost = self._user_input.get("gas_cost_m3", 0.0)
        thermostat_temp = self._user_input.get(CONF_THERMOSTAT_SET_TEMP, DEFAULT_THERMOSTAT_SET_TEMP)
        elec_max_temp = self._user_input.get(CONF_ELEC_BOILER_MAX_TEMP, DEFAULT_ELEC_BOILER_MAX_TEMP)
        gas_max_temp = self._user_input.get(CONF_GAS_BOILER_MAX_TEMP, DEFAULT_GAS_BOILER_MAX_TEMP)
        boiler_warm_diff = self._user_input.get(CONF_BOILER_WARM_DIFF, DEFAULT_BOILER_WARM_DIFF)

        schema_dict[vol.Required(CONF_THERMOSTAT_SET_TEMP, default=thermostat_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=60.0, step=0.5, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required(CONF_ELEC_BOILER_MAX_TEMP, default=elec_max_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=75.0, step=1.0, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required(CONF_GAS_BOILER_MAX_TEMP, default=gas_max_temp)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=30.0, max=70.0, step=1.0, mode=selector.NumberSelectorMode.SLIDER)
        )
        schema_dict[vol.Required(CONF_BOILER_WARM_DIFF, default=boiler_warm_diff)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1.0, max=15.0, step=0.5, mode=selector.NumberSelectorMode.BOX)
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

        # Optional: CONF_TOTAL_WATER_METER_SENSOR (Total Water Meter in m³ or Liters)
        val_meter = get_value(CONF_TOTAL_WATER_METER_SENSOR)
        if val_meter:
            schema_dict[vol.Optional(CONF_TOTAL_WATER_METER_SENSOR, default=val_meter)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional(CONF_TOTAL_WATER_METER_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
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

        # Optional: CONF_PEOPLE_HOME_SENSOR
        val_people = get_value(CONF_PEOPLE_HOME_SENSOR)
        if val_people:
            schema_dict[vol.Optional(CONF_PEOPLE_HOME_SENSOR, default=val_people)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "zone"])
            )
        else:
            schema_dict[vol.Optional(CONF_PEOPLE_HOME_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "zone"])
            )

        return self.async_show_form(
            step_id="boiler",
            data_schema=vol.Schema(schema_dict)
        )
