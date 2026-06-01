import sys
import os
import json
import types

# Create empty ems module
ems_mod = types.ModuleType('ems')
ems_mod.__path__ = [os.path.abspath("custom_components/ems")]
sys.modules['ems'] = ems_mod

from ems.boiler_dp_engine import run_boiler_dp, get_lut_rate

# Prices from logs
buy_prices = [
    0.993834, 0.952789, 0.92873, 0.91889, 0.911104, 0.904351, 
    0.835225, 0.727896, 0.496656, 0.260225, 0.21164, 0.07329, 
    -0.05479, -0.14629, -0.108172, 0.088394, 0.259093, 0.395525, 
    0.814217, 0.972653, 1.06034, 1.072369, 1.007487, 0.936811
]

sell_prices = [
    0.732994, 0.691949, 0.66789, 0.65805, 0.650264, 0.643511, 
    0.574385, 0.467056, 0.235816, 0.0, 0.0, 0.0, 
    0.0, 0.0, 0.0, 0.0, 0.0, 0.134685, 
    0.553377, 0.711813, 0.7995, 0.811529, 0.746647, 0.675971
]

physical_modes = ["buy"] * 24

slots = []
for h in range(24):
    slots.append({
        "date": "2026-05-24",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "physical_mode": physical_modes[h],
        "expected_soc": 50.0
    })

t_gas_start = 20.0
t_elec_start = 33.7
t_min = 40.0
t_max_elec = 70.0
t_max_gas = 50.0
vol_elec = 100.0
vol_gas = 100.0
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

test_slots = slots[12:15]

print("=== Simulation from Hour 12 ===")
status, schedule, stats = run_boiler_dp(
    test_slots,
    t_gas_start,
    t_elec_start,
    True,
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
    print(f"Hour {s['hour']}: mode={s['mode']} temp={s['temp_start']}->{s['temp_end']} (Gas {s['temp_gas_start']}->{s['temp_gas_end']}, Elec {s['temp_elec_start']}->{s['temp_elec_end']}) cost={s['cost']} energy={s['energy']}")

print("\n=== Detailed Hour 12 Transition Options Analysis ===")
T_gas_prev = t_gas_start
T_elec_prev = t_elec_start
T_bypass_prev = True
tariff = buy_prices[12]
total_vol = vol_gas + vol_elec

R_gas = get_lut_rate(cal_data["standby_losses"]["gas"], T_gas_prev)
R_elec = get_lut_rate(cal_data["standby_losses"]["elec"], T_elec_prev)
T_gas_cooled = max(20.0, T_gas_prev - R_gas)
T_elec_cooled = max(20.0, T_elec_prev - R_elec)

print(f"Cooled: Gas={T_gas_cooled:.2f}°C, Elec={T_elec_cooled:.2f}°C")

for mode in ["IDLE", "PUMP_ONLY", "GAS", "ELEC", "ELEC_PUMP"]:
    if mode == "IDLE":
        T_active = T_elec_cooled
        penalty = 1000.0 * max(0.0, t_min - T_active)
        cost = 0.0
        print(f"Mode {mode}: ActiveTemp={T_active:.2f}°C, Cost={cost:.4f}, Penalty={penalty:.2f}, Total={cost+penalty:.4f}")
    elif mode == "PUMP_ONLY":
        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol
        T_active = T_mixed
        penalty = 1000.0 * max(0.0, t_min - T_active)
        cost = 0.1 * tariff
        print(f"Mode {mode}: ActiveTemp={T_active:.2f}°C, Cost={cost:.4f}, Penalty={penalty:.2f}, Total={cost+penalty:.4f}")
    elif mode == "ELEC_PUMP":
        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol
        heat_rise = max(0.0, 40.0 - T_mixed)
        kwh = heat_rise / cal_data["elec_with_pump"]["efficiency_c_per_kwh"]
        cost = kwh * tariff
        penalty = 0.0
        print(f"Mode {mode} (to 40°C): ActiveTemp=40.00°C, Cost={cost:.4f}, Penalty={penalty:.2f}, Total={cost+penalty:.4f}")
