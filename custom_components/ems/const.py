"""Constants for the Energy Management System (EMS) integration."""
from dataclasses import dataclass

DOMAIN = "ems"
VERSION = "0.1.6"

# Configuration and options keys
CONF_TOTAL_LOAD_CONSUMPTION = "total_load_consumption"
CONF_TOTAL_GRID_EXPORT = "total_grid_export"
CONF_TOTAL_GRID_IMPORT = "total_grid_import"
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
CONF_MIN_SELL_PRICE = "min_sell_price"
CONF_MIN_ENERGY_TO_DISCHARGE = "min_energy_to_discharge"

# Battery optimization configuration keys
CONF_BAT_PRICE = "bat_price"
CONF_BAT_CYCLES = "bat_cycles"
CONF_BAT_CAPACITY_ENTITY = "bat_capacity_entity"
CONF_BAT_MAX_POWER = "bat_max_power"
CONF_BAT_CUR_POWER_ENTITY = "bat_cur_power_entity"
CONF_BAT_SOC_ENTITY = "bat_soc_entity"
CONF_BAT_VOLTAGE = "bat_voltage"
CONF_MIN_BAT_SOC = "min_bat_soc"

# Default values
DEFAULT_STATISTICS_DAYS = 14
DEFAULT_FALLBACK_CONSUMPTION = 0.5
DEFAULT_DEBUG = False

DEFAULT_SYSTEM_COST = 0.0
DEFAULT_MIN_SELL_PRICE = 0.0
DEFAULT_MIN_ENERGY_TO_DISCHARGE = 0.0
DEFAULT_BAT_PRICE = 0.0
DEFAULT_BAT_CYCLES = 6000
DEFAULT_BAT_MAX_POWER = 3000.0
DEFAULT_MIN_BAT_SOC = 20.0

# Hysteresis configuration
SOC_HYSTERESIS = 2.0

@dataclass
class InverterModeClass:
    """Defines algorithmic behavior for a specific inverter mode."""
    name: str
    pv_to_house: bool         # Солнце идет на покрытие потребления дома
    charge_from_pv: bool      # Заряд АКБ от солнечных панелей
    charge_from_grid: bool    # Заряд АКБ напрямую из сети
    discharge_to_house: bool  # Разряд АКБ для покрытия потребления дома
    discharge_to_grid: bool   # Разряд АКБ на продажу в сеть (Арбитраж)
    export_pv_to_grid: bool   # Продажа излишков солнца в сеть
    is_grid_bypass: bool      # Питание дома напрямую из сети (байпас)
    curtail_pv: bool          # Принудительное ограничение (зажим) генерации панелей
    calibration_limit_soc: float # Лимит SOC, выше которого генерация не используется для калибровки точности

# Глобальный реестр режимов для симуляции и логики
INVERTER_MODES = {
    "buy": InverterModeClass(
        name="buy",
        pv_to_house=True,
        charge_from_pv=True,
        charge_from_grid=True,
        discharge_to_house=False,
        discharge_to_grid=False,
        export_pv_to_grid=False,
        is_grid_bypass=True,
        curtail_pv=False,
        calibration_limit_soc=100.0
    ),
    "no_pv_sale_no_bat": InverterModeClass(
        name="no_pv_sale_no_bat",
        pv_to_house=True,
        charge_from_pv=False,
        charge_from_grid=False,
        discharge_to_house=False,
        discharge_to_grid=False,
        export_pv_to_grid=False,
        is_grid_bypass=False,
        curtail_pv=True,
        calibration_limit_soc=0.0
    ),
    "sale_pv_no_bat": InverterModeClass(
        name="sale_pv_no_bat",
        pv_to_house=True,
        charge_from_pv=False,
        charge_from_grid=False,
        discharge_to_house=False,
        discharge_to_grid=False,
        export_pv_to_grid=True,
        is_grid_bypass=False,
        curtail_pv=False,
        calibration_limit_soc=100.0
    ),
    "sale_pv_bat": InverterModeClass(
        name="sale_pv_bat",
        pv_to_house=True,
        charge_from_pv=False,
        charge_from_grid=False,
        discharge_to_house=True,
        discharge_to_grid=True,
        export_pv_to_grid=True,
        is_grid_bypass=False,
        curtail_pv=False,
        calibration_limit_soc=100.0
    ),
    "stop_sale": InverterModeClass(
        name="stop_sale",
        pv_to_house=True,
        charge_from_pv=True,
        charge_from_grid=False,
        discharge_to_house=True,
        discharge_to_grid=False,
        export_pv_to_grid=False,
        is_grid_bypass=False,
        curtail_pv=True,
        calibration_limit_soc=90.0
    ),
    "sale_pv": InverterModeClass(
        name="sale_pv",
        pv_to_house=True,
        charge_from_pv=True,
        charge_from_grid=False,
        discharge_to_house=True,
        discharge_to_grid=False,
        export_pv_to_grid=True,
        is_grid_bypass=False,
        curtail_pv=False,
        calibration_limit_soc=100.0
    ),
    "bat_emergency": InverterModeClass(
        name="bat_emergency",
        pv_to_house=True,
        charge_from_pv=True,
        charge_from_grid=False,
        discharge_to_house=False,
        discharge_to_grid=False,
        export_pv_to_grid=False,
        is_grid_bypass=True,
        curtail_pv=False,
        calibration_limit_soc=100.0
    ),
    "idle": InverterModeClass(
        name="idle",
        pv_to_house=True,
        charge_from_pv=False,
        charge_from_grid=False,
        discharge_to_house=False,
        discharge_to_grid=False,
        export_pv_to_grid=True,
        is_grid_bypass=True,
        curtail_pv=False,
        calibration_limit_soc=100.0
    )
}
