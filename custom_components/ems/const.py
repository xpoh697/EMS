"""Constants for the Energy Management System (EMS) integration."""
from dataclasses import dataclass

DOMAIN = "ems"
VERSION = "0.3.65"

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
CONF_VACATION_MODE_ENTITY = "vacation_mode_entity"
CONF_CALIBRATION_TYPE = "calibration_type"
CONF_WATER_FLOW_SENSOR = "water_flow_sensor"
CONF_PEOPLE_HOME_SENSOR = "people_home_sensor"

CONF_HW_CIRCULATION_PUMP = "hw_circulation_pump"
CONF_HW_CIRCULATION_RETURN_TEMP = "hw_circulation_return_temp"

# Thermostat/Boiler settings
CONF_THERMOSTAT_SET_TEMP = "thermostat_set_temp"
CONF_ELEC_BOILER_MAX_TEMP = "elec_boiler_max_temp"
CONF_GAS_BOILER_MAX_TEMP = "gas_boiler_max_temp"
CONF_BOILER_WARM_DIFF = "boiler_warm_diff"

# Financial configuration keys
CONF_PRICE_BUY_SENSOR = "price_buy_sensor"
CONF_PRICE_SELL_SENSOR = "price_sell_sensor"
CONF_SYSTEM_COST = "system_cost"
CONF_MIN_SELL_PRICE = "min_sell_price"
CONF_MIN_DISCHARGE_PRICE = "min_discharge_price"
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
CONF_MIN_BAT_SOC_EVENING = "min_bat_soc_evening"
CONF_MIN_BAT_SOC_MORNING = "min_bat_soc_morning"
CONF_BAT_SOC_EMERGENCY = "bat_soc_emergency"

# Default values
DEFAULT_STATISTICS_DAYS = 14
DEFAULT_FALLBACK_CONSUMPTION = 0.5
DEFAULT_DEBUG = False

DEFAULT_SYSTEM_COST = 0.0
DEFAULT_MIN_SELL_PRICE = 0.0
DEFAULT_MIN_DISCHARGE_PRICE = 0.0
DEFAULT_MIN_ENERGY_TO_DISCHARGE = 0.0
DEFAULT_BAT_PRICE = 0.0
DEFAULT_BAT_CYCLES = 6000
DEFAULT_BAT_MAX_POWER = 3000.0
DEFAULT_MIN_BAT_SOC = 20.0
DEFAULT_MIN_BAT_SOC_EVENING = 15.0
DEFAULT_MIN_BAT_SOC_MORNING = 15.0
DEFAULT_BAT_SOC_EMERGENCY = 10.0

DEFAULT_THERMOSTAT_SET_TEMP = 45.0
DEFAULT_ELEC_BOILER_MAX_TEMP = 70.0
DEFAULT_GAS_BOILER_MAX_TEMP = 50.0
DEFAULT_BOILER_WARM_DIFF = 6.0

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
    allow_boiler: bool        # Разрешена ли работа бойлера в данном режиме
    calibration_limit_soc: float # Лимит SOC, выше которого генерация не используется для калибровки точности

    def __post_init__(self) -> None:
        """Validate input types."""
        if not isinstance(self.allow_boiler, bool):
            raise TypeError(f"allow_boiler must be a bool, got {type(self.allow_boiler)}")
        if not isinstance(self.calibration_limit_soc, (int, float)):
            raise TypeError(f"calibration_limit_soc must be a float, got {type(self.calibration_limit_soc)}")

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
	allow_boiler=True,
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
	allow_boiler=True,
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
	allow_boiler=False,
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
	allow_boiler=False,
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
	allow_boiler=True,
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
	allow_boiler=True,
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
	allow_boiler=False,
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
	allow_boiler=True,
        calibration_limit_soc=100.0
    )
}

# Пресеты теплопотерь бойлеров для брэкетов Ньютона-Рихмана
STANDBY_LOSSES_PRESETS = {
    "gas": {
        "75_70": 4.3600,
        "70_65": 3.6300,
        "65_60": 2.9000,
        "60_55": 2.1700,
        "55_50": 1.8000,
        "50_45": 1.4320,
        "45_40": 0.6985,
        "40_35": 0.5100,
        "35_30": 0.3800,
        "30_25": 0.2500,
        "25_20": 0.1200,
    },
    "elec": {
        "75_70": 0.5000,
        "70_65": 0.4750,
        "65_60": 0.4500,
        "60_55": 0.4250,
        "55_50": 0.4000,
        "50_45": 0.3750,
        "45_40": 0.3500,
        "40_35": 0.3250,
        "35_30": 0.3000,
        "30_25": 0.2750,
        "25_20": 0.2500,
    },
}
