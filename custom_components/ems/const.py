"""Constants for the Energy Management System (EMS) integration."""

DOMAIN = "ems"
VERSION = "0.1.0"

# Configuration and options keys
CONF_TOTAL_LOAD_CONSUMPTION = "total_load_consumption"
CONF_CURRENT_HOUSE_CONSUMPTION = "current_house_consumption"
CONF_CURRENT_PV_GENERATION = "current_pv_generation"
CONF_PV_GENERATION_TODAY = "pv_generation_today"
CONF_PV_FORECAST_TODAY = "pv_forecast_today"
CONF_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow"
CONF_STATISTICS_DAYS = "statistics_days"
CONF_FALLBACK_CONSUMPTION = "fallback_consumption"
CONF_DEBUG = "debug"

# Default values
DEFAULT_STATISTICS_DAYS = 14
DEFAULT_FALLBACK_CONSUMPTION = 0.5
DEFAULT_DEBUG = False
