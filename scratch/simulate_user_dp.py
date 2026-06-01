import sys
sys.path.append('.')
from custom_components.ems.dp_engine import run_unified_dp, DPConfig

# Configure parameters
buy_prices = [0.948287, 0.87814, 0.85739, 0.849899, 0.849887, 0.885606, 1.380878, 1.340165, 1.204582, 0.807083, 0.682853, 0.68273, 0.680393, 0.673689, 0.673689, 0.257162, 0.260852, 0.883355, 1.23024, 1.444223, 1.593324, 1.568453, 1.044399, 0.958607]
sell_prices = [0.687447, 0.6173, 0.59655, 0.589059, 0.589047, 0.624766, 0.698025, 0.657312, 0.521729, 0.12423, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2e-05, 0.200502, 0.547387, 0.76137, 0.910471, 0.8856, 0.783559, 0.697767]

# Let's assume a generic daylight solar curve totaling ~37 kWh
pv_today = [0.0]*6 + [0.5, 1.5, 3.0, 4.5, 5.5, 6.0, 6.0, 5.0, 3.5, 2.0, 1.0, 0.2] + [0.0]*6
consumption_today = [0.5] * 24

# Create slots starting at hour 0 (to simulate a full 24h run)
slots = []
for h in range(24):
    override = None
    if h == 9:
        override = "sale_pv" # User manual override
    slots.append({
        "date": "2026-05-27",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "pv_kwh": pv_today[h],
        "consumption_kwh": consumption_today[h],
        "override": override
    })

capacity = 17.1
min_bat_soc = 15.0
soc = 30.0

usable_capacity = capacity * (1 - min_bat_soc / 100.0)
current_usable = capacity * (soc - min_bat_soc) / 100.0
cycle_cost = 0.073099
terminal_value = 0.0
min_end_usable = 0.0

dp_config = DPConfig(
    min_sell_price=0.0,
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
    cycle_cost=cycle_cost,
    terminal_value_per_kwh=terminal_value,
    min_end_usable=min_end_usable,
    config=dp_config,
    remaining_hour_fraction=1.0,
)

trajectory = stats.get("expected_trajectory", [])
print(f"Stats summary: {stats}")
print("Expected trajectory:")
for h in range(24):
    print(f"  Hour {h:02d}: SOC={trajectory[h]}%")
