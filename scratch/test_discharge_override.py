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

# Load custom_components/ems/dp_engine.py manually
with open(os.path.join(os.path.dirname(__file__), '../custom_components/ems/dp_engine.py'), 'r', encoding='utf-8') as f:
    code_content = f.read()

# Mock INVERTER_MODES and map_dp_to_physical
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

# Use the real map_dp_to_physical logic here for testing
def map_dp_to_physical(action, sell_price, pv_kwh, min_sell_price, min_discharge_price, cheap_ahead):
    if action in (None, "unknown", "unavailable", "buy", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "bat_emergency"):
        return action, "direct_mapping"
    if action == "discharge":
        return "sale_pv_bat", "test"
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

slots = []

# Hour 10
slots.append({
    "date": "2026-05-27",
    "hour": 10,
    "buy_price": 0.68,
    "sell_price": 0.00,
    "pv_kwh": 3.55,
    "consumption_kwh": 0.50,
    "override": None
})

# Hour 11
slots.append({
    "date": "2026-05-27",
    "hour": 11,
    "buy_price": 0.68,
    "sell_price": 0.00,
    "pv_kwh": 3.55,
    "consumption_kwh": 0.50,
    "override": "discharge:19.0"
})

# Hour 12 to 23
for h in range(12, 24):
    slots.append({
        "date": "2026-05-27",
        "hour": h,
        "buy_price": 0.68,
        "sell_price": 0.00,
        "pv_kwh": 0.0,
        "consumption_kwh": 0.50,
        "override": None
    })

capacity = 14.0
min_bat_soc = 15.0
soc = 24.0

usable_capacity = capacity * (1 - min_bat_soc / 100.0)
current_usable = capacity * (soc - min_bat_soc) / 100.0

dp_config = DPConfig(
    min_sell_price=0.0,
    min_discharge_price=0.1,  # Sell price 0.0 is less than min_discharge_price 0.1
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
    terminal_value_per_kwh=0.0,
    min_end_usable=0.0,
    config=dp_config,
    remaining_hour_fraction=1.0,
)

print(f"Stats summary: {stats}")
print("Expected trajectory:")
for idx, s in enumerate(slots):
    print(f"  Hour {s['hour']}: Start SOC={stats['expected_trajectory'][idx]}%, Override={s['override']}")
