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

def map_dp_to_physical(action, sell_price, pv_kwh, min_sell_price, min_discharge_price, cheap_ahead):
    return action, "mocked"

# Load and execute dp_engine.py
with open(os.path.join(os.path.dirname(__file__), '../custom_components/ems/dp_engine.py'), 'r', encoding='utf-8') as f:
    code_content = f.read()

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

# Extract local constants and run_unified_dp
run_unified_dp = context['run_unified_dp']
DPConfig = context['DPConfig']
cvcc_multipliers = [] # we will duplicate it here to test expected_nsi vs nsi
ACT_GRID_CHARGE = context['ACT_GRID_CHARGE']
ACT_PV_CHARGE = context['ACT_PV_CHARGE']
ACT_SOL = context['ACT_SOL']

# Test parameters
capacity = 5.12
min_bat_soc = 20.0
usable_capacity = capacity * (1 - min_bat_soc / 100.0) # 4.096
energy_step = 0.1
max_energy_idx = int(round(usable_capacity / energy_step)) # 41
cvcc_multipliers = [1.0] * (max_energy_idx + 1)
# fill multipliers according to get_cvcc_charge_multiplier in dp_engine.py
get_cvcc = context['get_cvcc_charge_multiplier']
for state in range(max_energy_idx + 1):
    clamped_soc = min_bat_soc + (state * energy_step / capacity) * 100.0
    cvcc_multipliers[state] = get_cvcc(clamped_soc)

# Let's test different combinations of battery power, target_soc, and starting state
battery_max_charge_power = 6.6
target_socs = [54.5, 59.3, 59.5, 80.0, 100.0]

for target_soc in target_socs:
    target_usable = capacity * (target_soc - min_bat_soc) / 100.0
    target_nsi = max(0, min(max_energy_idx, int(round(target_usable / energy_step))))
    
    print(f"\n--- Testing target_soc={target_soc}% (target_nsi={target_nsi}) ---")
    
    for state_idx in range(max_energy_idx + 1):
        if target_nsi <= state_idx:
            # We want to discharge or stay idle, skip charge test
            continue
            
        # Expected nsi calculation (our code)
        max_charge_power = battery_max_charge_power * cvcc_multipliers[state_idx]
        usable_energy = state_idx * energy_step
        avail_cap = usable_capacity - usable_energy
        max_gc = min(max_charge_power, avail_cap)
        max_possible_chg_steps_expected = int(max_gc / energy_step)
        expected_nsi = min(target_nsi, state_idx + max_possible_chg_steps_expected)
        
        # Grid Charge calculation inside dp_engine.py
        usable_energy = state_idx * energy_step
        avail_cap = usable_capacity - usable_energy
        max_gc = min(max_charge_power, avail_cap)
        max_possible_chg_steps_actual = int(max_gc / energy_step)
        desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps_actual)
        
        actual_nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
        
        if actual_nsi != expected_nsi:
            print(f"  MISMATCH at state_idx={state_idx} (SOC={min_bat_soc + (state_idx*energy_step/capacity)*100.0:.1f}%)")
            print(f"    expected_nsi={expected_nsi} (max_possible_chg_steps_expected={max_possible_chg_steps_expected})")
            print(f"    actual_nsi={actual_nsi} (max_possible_chg_steps_actual={max_possible_chg_steps_actual}, desired_chg_steps={desired_chg_steps})")
            print(f"    avail_cap={avail_cap:.4f}, max_gc={max_gc:.4f}")
            
print("\nDone testing mismatch.")
