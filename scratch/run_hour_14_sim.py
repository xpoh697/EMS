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

# Mock the imports
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

def map_override_to_physical(action, sell_price, pv_kwh, min_sell_price, min_discharge_price, cheap_ahead):
    return map_dp_to_physical(action, sell_price, pv_kwh, min_sell_price, min_discharge_price, cheap_ahead)

code_content = code_content.replace('from .const import INVERTER_MODES', '')
code_content = code_content.replace('from .utils import map_dp_to_physical, map_override_to_physical', '')

context = {
    'INVERTER_MODES': INVERTER_MODES,
    'map_dp_to_physical': map_dp_to_physical,
    'map_override_to_physical': map_override_to_physical,
    'sys': sys,
    'os': os,
    'ModuleType': ModuleType,
}

exec(code_content, context)

run_unified_dp = context['run_unified_dp']
DPConfig = context['DPConfig']

# Hourly prices from logs
buy_prices = [0.977586, 0.93734, 0.916799, 0.909788, 0.92012, 0.95087, 1.519253, 1.564849, 1.495637, 1.413338, 1.260498, 1.249637, 1.237226, 1.249514, 1.247534, 0.827501, 0.83402, 1.359353, 1.548957, 2.012495, 2.645822, 2.525516, 1.47854, 1.113439]
sell_prices = [0.716746, 0.6765, 0.655959, 0.648948, 0.65928, 0.69003, 0.8364, 0.881996, 0.812784, 0.730485, 0.577645, 0.566784, 0.566784, 0.566661, 0.564681, 0.566661, 0.57318, 0.6765, 0.866104, 1.329642, 1.962969, 1.842663, 1.2177, 0.852599]

# Simulation starting at 14:00 (hour 14)
# Let's assume some PV generation at 14:00 and 15:00
# And typical house load
slots = []
for h in range(14, 24):
    pv_kwh = 0.0
    if h == 14:
        pv_kwh = 4.0 # Good PV at 14:00
    elif h == 15:
        pv_kwh = 0.5
    elif h == 16:
        pv_kwh = 0.5
    
    slots.append({
        "date": "2026-06-01",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "pv_kwh": pv_kwh,
        "consumption_kwh": 0.5, # typical load
        "planned_boiler_kwh": 0.0,
        "override": None
    })

capacity = 17.0
min_bat_soc = 15.0
soc = 91.0 # Starting SOC 91%

usable_capacity = capacity * (1 - min_bat_soc / 100.0) # 17 * 0.85 = 14.45
current_usable = capacity * (soc - min_bat_soc) / 100.0 # 17 * 0.76 = 12.92

# Reserve calculation
night_hours = [23, 0, 1, 2, 3, 4, 5, 6, 7]
profile = [0.5] * 24
reserve = sum(profile[h] for h in night_hours if h < len(profile)) # 9 * 0.5 = 4.5
min_end_usable = min(reserve, usable_capacity)

horizon_buy = [slot["buy_price"] for slot in slots]
global_min_buy = min(horizon_buy) if horizon_buy else 0.0
terminal_value = max(0.1, global_min_buy + 0.073529)

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
    cycle_cost=0.073529,
    terminal_value_per_kwh=terminal_value,
    min_end_usable=min_end_usable,
    config=dp_config,
    remaining_hour_fraction=1.0,
)

print(f"Expected trajectory SOC: {stats.get('expected_trajectory', [])}")
print("Detailed schedule plan:")
trajectory = stats.get("expected_trajectory", [])
for idx, slot in enumerate(slots):
    action = "idle"
    key = (slot["date"], slot["hour"])
    if any(h["hour"] == slot["hour"] for h in chg_h): action = "grid_charge"
    elif any(h["hour"] == slot["hour"] for h in dis_h): action = "discharge"
    elif any(h["hour"] == slot["hour"] for h in pvc_h): action = "pv_charge"
    elif any(h["hour"] == slot["hour"] for h in sc_h): action = "self_consume"
    
    start_soc = trajectory[idx] if idx < len(trajectory) else 0.0
    end_soc = trajectory[idx+1] if idx+1 < len(trajectory) else 0.0
    print(f"Hour {slot['hour']}: Buy={slot['buy_price']:.2f} Sell={slot['sell_price']:.2f} PV={slot['pv_kwh']:.2f} Cons={slot['consumption_kwh']:.2f} | Action={action} | SOC: {start_soc}% -> {end_soc}%")
