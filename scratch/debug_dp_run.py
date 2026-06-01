import sys
import os
from types import ModuleType

# Setup sys.modules mocks
ha_mock = ModuleType('homeassistant')
sys.modules['homeassistant'] = ha_mock
util_mock = ModuleType('homeassistant.util')
sys.modules['homeassistant.util'] = util_mock

class MockDt:
    @staticmethod
    def now():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

util_mock.dt = MockDt

# Load and execute custom_components/ems/dp_engine.py manually
with open(os.path.join(os.path.dirname(__file__), '../custom_components/ems/dp_engine.py'), 'r', encoding='utf-8') as f:
    code_content = f.read()

# Mock the imports inside dp_engine.py
class InverterModeClass:
    def __init__(self, name, pv_to_house, charge_from_pv, charge_from_grid, discharge_to_house, discharge_to_grid, export_pv_to_grid, is_grid_bypass, curtail_pv, allow_boiler, calibration_limit_soc):
        self.name = name
        self.pv_to_house = pv_to_house
        self.charge_from_pv = charge_from_pv
        self.charge_from_grid = charge_from_grid
        self.discharge_to_house = discharge_to_house
        self.discharge_to_grid = discharge_to_grid
        self.export_pv_to_grid = export_pv_to_grid
        self.is_grid_bypass = is_grid_bypass
        self.curtail_pv = curtail_pv
        self.allow_boiler = allow_boiler
        self.calibration_limit_soc = calibration_limit_soc

INVERTER_MODES = {
    "buy": InverterModeClass("buy", True, True, True, False, False, False, True, False, True, 100.0),
    "no_pv_sale_no_bat": InverterModeClass("no_pv_sale_no_bat", True, False, False, False, False, False, False, True, True, 0.0),
    "sale_pv_no_bat": InverterModeClass("sale_pv_no_bat", True, False, False, False, False, True, False, False, False, 100.0),
    "sale_pv_bat": InverterModeClass("sale_pv_bat", True, False, False, True, True, True, False, False, False, 100.0),
    "sale_pv": InverterModeClass("sale_pv", True, True, False, True, False, True, False, False, True, 100.0),
    "stop_sale": InverterModeClass("stop_sale", True, True, False, True, False, False, False, True, True, 90.0),
}

def map_dp_to_physical(action, sell_price, pv_kwh, min_sell_price, min_discharge_price, cheap_ahead):
    if action == "grid_charge":
        return "buy", "override"
    if action == "sale_pv":
        return "sale_pv", "override"
    return "stop_sale", "fallback"

code_content = code_content.replace('from .const import INVERTER_MODES', '')
code_content = code_content.replace('from .utils import map_dp_to_physical', '')

context = {
    'INVERTER_MODES': INVERTER_MODES,
    'map_dp_to_physical': map_dp_to_physical,
    'sys': sys,
    'os': os,
    'ModuleType': ModuleType,
}

exec(code_content, context)

run_unified_dp = context['run_unified_dp']
DPConfig = context['DPConfig']

buy_prices = [0.948287, 0.87814, 0.85739, 0.849899, 0.849887, 0.885606, 1.380878, 1.340165, 1.204582, 0.807083, 0.682853, 0.68273, 0.680393, 0.673689, 0.673689, 0.257162, 0.260852, 0.883355, 1.23024, 1.444223, 1.593324, 1.568453, 1.044399, 0.958607]
sell_prices = [0.687447, 0.6173, 0.59655, 0.589059, 0.589047, 0.624766, 0.698025, 0.657312, 0.521729, 0.12423, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2e-05, 0.200502, 0.547387, 0.76137, 0.910471, 0.8856, 0.783559, 0.697767]

current_hour = 9
slots = []
for h in range(current_hour, 24):
    override = None
    if h == 11:
        override = "grid_charge:100.0"
    slots.append({
        "date": "2026-05-27",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "pv_kwh": 1.5 if h == 9 else (2.5 if h == 10 else (3.0 if h == 11 else 0.5)),
        "consumption_kwh": 0.12 if h == 9 else 0.0,
        "override": override
    })

capacity = 14.0
min_bat_soc = 15.0
soc = 40.0

usable_capacity = capacity * (1 - min_bat_soc / 100.0)
current_usable = capacity * (soc - min_bat_soc) / 100.0

dp_config = DPConfig(
    min_sell_price=0.1,
    min_discharge_price=0.0,
    battery_max_discharge_power=3.0,
    battery_max_charge_power=3.0,
    battery_min_soc=int(min_bat_soc),
    battery_capacity=capacity,
    min_energy_to_discharge=0.0,
    disable_discharge=False,
)

chg_h, dis_h, pvc_h, sc_h, pim_h, stats = run_unified_dp(
    slots=slots,
    current_usable=current_usable,
    usable_capacity=usable_capacity,
    cycle_cost=0.089,
    terminal_value_per_kwh=0.33,
    min_end_usable=0.0,
    config=dp_config,
    remaining_hour_fraction=1.0,
)

print(f"Stats: {stats}")
print("\nSchedule plan:")
for idx, item in enumerate(stats.get("expected_trajectory", [])):
    slot = slots[idx]
    print(f"Hour {slot['hour']}: Start SOC={item}%, PlanAction={slot.get('override') or 'optimized'}")
