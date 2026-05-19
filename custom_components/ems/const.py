"""Constants for the Energy Management System (EMS) integration."""

DOMAIN = "ems"
VERSION = "0.1.0"

# Configuration and options keys
CONF_TOTAL_LOAD_CONSUMPTION = "total_load_consumption"
CONF_CURRENT_HOUSE_CONSUMPTION = "current_house_consumption"
CONF_INVERTER_MODES_LIST = "inverter_modes_list"
CONF_CURRENT_PV_GENERATION = "current_pv_generation"
CONF_PV_GENERATION_TODAY = "pv_generation_today"
CONF_PV_FORECAST_TODAY = "pv_forecast_today"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow"
CONF_STATISTICS_DAYS = "statistics_days"
CONF_FALLBACK_CONSUMPTION = "fallback_consumption"
CONF_DEBUG = "debug"

# Financial configuration keys
CONF_PRICE_BUY_SENSOR = "price_buy_sensor"
CONF_PRICE_SELL_SENSOR = "price_sell_sensor"
CONF_SYSTEM_COST = "system_cost"

# Battery optimization configuration keys
CONF_BAT_PRICE = "bat_price"
CONF_BAT_CYCLES = "bat_cycles"
CONF_BAT_CAPACITY_ENTITY = "bat_capacity_entity"
CONF_BAT_MAX_POWER = "bat_max_power"
CONF_BAT_CUR_POWER_ENTITY = "bat_cur_power_entity"
CONF_BAT_VOLTAGE = "bat_voltage"

# Default values
DEFAULT_STATISTICS_DAYS = 14
DEFAULT_FALLBACK_CONSUMPTION = 0.5
DEFAULT_DEBUG = False

DEFAULT_SYSTEM_COST = 0.0
DEFAULT_BAT_PRICE = 0.0
DEFAULT_BAT_CYCLES = 6000
DEFAULT_BAT_MAX_POWER = 3000.0
DEFAULT_BAT_VOLTAGE = 48.0
