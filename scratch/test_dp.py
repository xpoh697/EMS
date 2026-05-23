import sys
import os
import json
import types

# Create empty ems module to avoid loading custom_components/ems/__init__.py
ems_mod = types.ModuleType('ems')
ems_mod.__path__ = [os.path.abspath("custom_components/ems")]
sys.modules['ems'] = ems_mod

# Now import run_boiler_dp
from ems.boiler_dp_engine import run_boiler_dp

buy_prices = [1.02344, 0.96194, 0.939677, 0.924154, 0.911252, 0.906073, 0.86723, 0.810576, 0.62369, 0.29774, 0.260852, 0.259093, 0.251676, 0.23624, 0.251676, 0.260828, 0.337186, 0.802926, 0.937623, 1.06649, 1.188285, 1.150007, 1.067597, 0.99761]
sell_prices = [0.7626, 0.7011, 0.678837, 0.663314, 0.650412, 0.645233, 0.60639, 0.549736, 0.36285, 0.0369, 1.2e-05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.076346, 0.542086, 0.676783, 0.80565, 0.927445, 0.889167, 0.806757, 0.73677]

physical_modes = ["idle"] * 24
for h in range(8, 17):
    physical_modes[h] = "sale_pv"

slots = []
for h in range(24):
    slots.append({
        "date": "2026-05-23",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "physical_mode": physical_modes[h],
        "expected_soc": 50.0
    })

t_start = 39.0
t_min = 40.0
t_max_elec = 70.0
t_max_gas = 65.0
vol_elec = 75.0
vol_gas = 45.0
gas_cost_m3 = 4.5

cal_data = {
  "gas_only": {
    "efficiency_c_per_m3": 338.1579,
    "last_calibrated": "2026-05-23"
  },
  "gas_with_pump": {
    "efficiency_c_per_m3": 103.8223,
    "last_calibrated": "2026-05-21"
  },
  "elec_only": {
    "efficiency_c_per_kwh": 20.4082,
    "last_calibrated": "2026-05-22"
  },
  "elec_with_pump": {
    "efficiency_c_per_kwh": 3.3505,
    "last_calibrated": "2026-05-22"
  },
  "standby_losses": {
    "gas": {
      "75_70": 4.36, "70_65": 3.63, "65_60": 2.9, "60_55": 2.17, "55_50": 1.8,
      "50_45": 1.432, "45_40": 0.6985, "40_35": 0.51, "35_30": 0.38, "30_25": 0.25, "25_20": 0.12
    },
    "elec": {
      "75_70": 0.5, "70_65": 0.475, "65_60": 0.45, "60_55": 0.425, "55_50": 0.4,
      "50_45": 0.375, "45_40": 0.35, "40_35": 0.325, "35_30": 0.3, "30_25": 0.275, "25_20": 0.25
    },
    "last_calibrated": "2026-05-23"
  }
}

# Run DP from hour 13 to the end of the day
test_slots = slots[13:]

status, schedule, stats = run_boiler_dp(
    test_slots,
    t_start,
    t_min,
    t_max_elec,
    t_max_gas,
    vol_elec,
    vol_gas,
    gas_cost_m3,
    cal_data
)

print("Status:", status)
print("Stats:", json.dumps(stats, indent=2))
print("Schedule:")
for s in schedule:
    print(f"Hour {s['hour']}: mode={s['mode']} temp={s['temp_start']}->{s['temp_end']} cost={s['cost']} energy={s['energy']}")
