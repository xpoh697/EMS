import sys
import os
import json
import types

# Create empty ems module to avoid loading custom_components/ems/__init__.py
ems_mod = types.ModuleType('ems')
ems_mod.__path__ = [os.path.abspath("custom_components/ems")]
sys.modules['ems'] = ems_mod

import ems.boiler_dp_engine
from ems.boiler_dp_engine import get_lut_rate
from ems.const import INVERTER_MODES

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

t_start = 42.5
t_min = 40.0
t_max_elec = 70.0
t_max_gas = 65.0
vol_elec = 75.0
vol_gas = 45.0
gas_cost_m3 = 4.5

cal_data = {
  "gas_only": { "efficiency_c_per_m3": 338.1579, "last_calibrated": "2026-05-23" },
  "gas_with_pump": { "efficiency_c_per_m3": 103.8223, "last_calibrated": "2026-05-21" },
  "elec_only": { "efficiency_c_per_kwh": 20.4082, "last_calibrated": "2026-05-22" },
  "elec_with_pump": { "efficiency_c_per_kwh": 3.3505, "last_calibrated": "2026-05-22" },
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

# Adjusted run_boiler_dp with a small reward for temperature to favor higher state values when cheap
def run_boiler_dp_adjusted(
    slots, t_start, t_min, t_max_elec, t_max_gas, vol_elec, vol_gas, gas_cost_m3, cal_data, temp_reward=0.002
):
    eff_gas_only = cal_data.get("gas_only", {}).get("efficiency_c_per_m3", 0.0)
    eff_gas_pump = cal_data.get("gas_with_pump", {}).get("efficiency_c_per_m3", 0.0)
    eff_elec_only = cal_data.get("elec_only", {}).get("efficiency_c_per_kwh", 0.0)
    eff_elec_pump = cal_data.get("elec_with_pump", {}).get("efficiency_c_per_kwh", 0.0)
    standby_losses = cal_data.get("standby_losses", {})

    t_max = max(t_max_elec, t_max_gas)
    num_states = int(round((t_max - t_min) * 2)) + 1
    t_start_clamped = max(t_min, min(t_start, t_max))
    start_idx = int(round((t_start_clamped - t_min) * 2))
    N = len(slots)

    # 1st pass: relax = False
    t_min_limit = t_min
    dp = [[float("inf")] * num_states for _ in range(N + 1)]
    prev_state = [[-1] * num_states for _ in range(N + 1)]
    prev_mode = [["IDLE"] * num_states for _ in range(N + 1)]
    prev_cost = [[0.0] * num_states for _ in range(N + 1)]
    prev_energy = [[0.0] * num_states for _ in range(N + 1)]
    dp[0][start_idx] = 0.0

    for h in range(1, N + 1):
        slot = slots[h - 1]
        buy_price = slot.get("buy_price", 0.0)
        sell_price = slot.get("sell_price", 0.0)
        mode_name = slot.get("physical_mode", "idle")
        soc = slot.get("expected_soc", 50.0)

        mode_config = INVERTER_MODES.get(mode_name)
        if mode_name == "buy":
            tariff = buy_price
        elif mode_name in ("sale_pv", "idle"):
            tariff = sell_price
        elif mode_config and mode_config.curtail_pv and soc >= mode_config.calibration_limit_soc:
            tariff = 0.0
        else:
            tariff = sell_price

        allow_boiler = getattr(mode_config, "allow_boiler", False) if mode_config else False
        allow_elec = allow_boiler or mode_name in ("sale_pv_bat", "sale_pv_no_bat")

        for prev_idx in range(num_states):
            if dp[h - 1][prev_idx] == float("inf"):
                continue
            T_prev = t_min + prev_idx * 0.5
            R_gas = get_lut_rate(standby_losses.get("gas", {}), T_prev)
            R_elec = get_lut_rate(standby_losses.get("elec", {}), T_prev)
            total_vol = vol_gas + vol_elec
            if total_vol > 0:
                R_sys = (R_gas * vol_gas + R_elec * vol_elec) / total_vol
            else:
                R_sys = (R_gas + R_elec) / 2.0
            T_cooled = max(20.0, T_prev - R_sys)

            for mode in ["IDLE", "GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]:
                if mode in ("GAS", "GAS_PUMP") and vol_gas <= 0.0:
                    continue
                if mode in ("ELEC", "ELEC_PUMP") and (vol_elec <= 0.0 or not allow_elec):
                    continue

                if mode == "IDLE":
                    max_rise = 0.25
                    t_max_mode = t_max
                elif mode == "GAS":
                    max_rise = 40.0
                    t_max_mode = t_max_gas
                elif mode == "GAS_PUMP":
                    max_rise = 40.0
                    t_max_mode = t_max_gas
                elif mode == "ELEC":
                    power_kw = cal_data.get("elec_only", {}).get("heater_power_kw", 2.5)
                    max_rise = power_kw * eff_elec_only * (vol_elec / total_vol) if total_vol > 0 else power_kw * eff_elec_only
                    t_max_mode = t_max_elec
                elif mode == "ELEC_PUMP":
                    power_kw = cal_data.get("elec_with_pump", {}).get("heater_power_kw", 2.5)
                    max_rise = power_kw * eff_elec_pump
                    t_max_mode = t_max_elec

                for curr_idx in range(num_states):
                    T_curr = t_min + curr_idx * 0.5
                    if T_curr < t_min_limit or T_curr > t_max_mode:
                        continue
                    delta_T = T_curr - T_cooled
                    if delta_T < -0.25 or delta_T > max_rise:
                        continue

                    if mode == "IDLE":
                        if delta_T > 0.25:
                            continue
                        cost = 0.0
                        energy = 0.0
                    elif mode == "GAS":
                        heat_rise = max(0.0, delta_T)
                        gas_qty = heat_rise * total_vol / (vol_gas * eff_gas_only) if vol_gas > 0 else heat_rise / eff_gas_only
                        cost = gas_qty * gas_cost_m3
                        energy = gas_qty
                    elif mode == "GAS_PUMP":
                        heat_rise = max(0.0, delta_T)
                        gas_qty = heat_rise / eff_gas_pump
                        cost = gas_qty * gas_cost_m3
                        energy = gas_qty
                    elif mode == "ELEC":
                        heat_rise = max(0.0, delta_T)
                        kwh = heat_rise * total_vol / (vol_elec * eff_elec_only) if vol_elec > 0 else heat_rise / eff_elec_only
                        cost = kwh * tariff
                        energy = kwh
                    elif mode == "ELEC_PUMP":
                        heat_rise = max(0.0, delta_T)
                        kwh = heat_rise / eff_elec_pump
                        cost = kwh * tariff
                        energy = kwh

                    # Add temperature reward to prioritize higher temperature states (soft comfort preference)
                    # We subtract the reward from cost (lower cost is better)
                    adjusted_cost = cost - temp_reward * (T_curr - t_min)

                    new_cost = dp[h - 1][prev_idx] + adjusted_cost
                    if new_cost < dp[h][curr_idx]:
                        dp[h][curr_idx] = new_cost
                        prev_state[h][curr_idx] = prev_idx
                        prev_mode[h][curr_idx] = mode
                        prev_cost[h][curr_idx] = cost
                        prev_energy[h][curr_idx] = energy

    best_idx = min(range(num_states), key=lambda i: dp[N][i])
    if dp[N][best_idx] == float("inf"):
        return "FAILED", [], {}

    path = []
    curr_idx = best_idx
    for h in range(N, 0, -1):
        prev_idx = prev_state[h][curr_idx]
        path.append({
            "hour": slots[h - 1]["hour"],
            "mode": prev_mode[h][curr_idx],
            "temp_start": round(t_min + prev_idx * 0.5, 2),
            "temp_end": round(t_min + curr_idx * 0.5, 2),
            "cost": round(prev_cost[h][curr_idx], 4),
            "energy": round(prev_energy[h][curr_idx], 4),
        })
        curr_idx = prev_idx
    path.reverse()
    return "OK", path, {}

status, schedule, _ = run_boiler_dp_adjusted(slots[13:], t_start, t_min, t_max_elec, t_max_gas, vol_elec, vol_gas, gas_cost_m3, cal_data, temp_reward=0.001)
print("With temp_reward = 0.001:")
for s in schedule:
    print(f"Hour {s['hour']}: mode={s['mode']} temp={s['temp_start']}->{s['temp_end']} cost={s['cost']} energy={s['energy']}")

status, schedule, _ = run_boiler_dp_adjusted(slots[13:], t_start, t_min, t_max_elec, t_max_gas, vol_elec, vol_gas, gas_cost_m3, cal_data, temp_reward=0.002)
print("\nWith temp_reward = 0.002:")
for s in schedule:
    print(f"Hour {s['hour']}: mode={s['mode']} temp={s['temp_start']}->{s['temp_end']} cost={s['cost']} energy={s['energy']}")

status, schedule, _ = run_boiler_dp_adjusted(slots[13:], t_start, t_min, t_max_elec, t_max_gas, vol_elec, vol_gas, gas_cost_m3, cal_data, temp_reward=0.005)
print("\nWith temp_reward = 0.005:")
for s in schedule:
    print(f"Hour {s['hour']}: mode={s['mode']} temp={s['temp_start']}->{s['temp_end']} cost={s['cost']} energy={s['energy']}")
