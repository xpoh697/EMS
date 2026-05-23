import sys
import os
import json
import math
from typing import Any, Dict, List, Tuple

# Re-implement run_boiler_dp here with the fix to test it
def get_lut_rate(lut: dict, temp: float) -> float:
    if not lut:
        return 0.0
    bracket_top = math.ceil(temp / 5.0) * 5.0
    bracket_bottom = bracket_top - 5.0
    if temp == bracket_top:
        bracket_top += 5.0
        bracket_bottom = bracket_top - 5.0
    key = f"{int(bracket_top)}_{int(bracket_bottom)}"
    rate = lut.get(key)
    if rate is not None and rate > 0:
        return float(rate)
    best_key = None
    best_delta = float("inf")
    for k, v in lut.items():
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        try:
            parts = k.split("_")
            k_top = int(parts[0])
            k_bot = int(parts[1])
            k_mid = (k_top + k_bot) / 2.0
            delta = abs(temp - k_mid)
            if delta < best_delta:
                best_delta = delta
                best_key = k
        except (ValueError, IndexError):
            continue
    if best_key:
        return float(lut[best_key])
    return 0.0

INVERTER_MODES = {
    "idle": type("InverterMode", (object,), {"curtail_pv": False, "allow_boiler": True, "calibration_limit_soc": 100.0})(),
    "sale_pv": type("InverterMode", (object,), {"curtail_pv": True, "allow_boiler": True, "calibration_limit_soc": 80.0})(),
}

def run_boiler_dp_fixed(
    slots: List[Dict[str, Any]],
    t_gas_start: float,
    t_elec_start: float,
    bypass_start: bool,
    t_min: float,
    t_max_elec: float,
    t_max_gas: float,
    vol_elec: float,
    vol_gas: float,
    gas_cost_m3: float,
    cal_data: Dict[str, Any],
    temp_reward: float = 0.001,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    eff_gas_only = cal_data.get("gas_only", {}).get("efficiency_c_per_m3", 0.0)
    eff_gas_pump = cal_data.get("gas_with_pump", {}).get("efficiency_c_per_m3", 0.0)
    eff_elec_only = cal_data.get("elec_only", {}).get("efficiency_c_per_kwh", 0.0)
    eff_elec_pump = cal_data.get("elec_with_pump", {}).get("efficiency_c_per_kwh", 0.0)
    standby_losses = cal_data.get("standby_losses", {})

    t_max = max(t_max_elec, t_max_gas)
    GRID_MIN_TEMP = min(20.0, t_min)
    num_states = int(round((t_max - GRID_MIN_TEMP) * 2)) + 1
    
    t_gas_start_clamped = max(GRID_MIN_TEMP, min(t_gas_start, t_max))
    start_idx = int(round((t_gas_start_clamped - GRID_MIN_TEMP) * 2))
    N = len(slots)

    for relax in [False, True]:
        t_min_limit = t_min if not relax else 20.0
        dp = [[float("inf")] * num_states for _ in range(N + 1)]
        prev_state = [[-1] * num_states for _ in range(N + 1)]
        prev_mode = [["IDLE"] * num_states for _ in range(N + 1)]
        prev_cost = [[0.0] * num_states for _ in range(N + 1)]
        prev_energy = [[0.0] * num_states for _ in range(N + 1)]
        elec_temp = [[GRID_MIN_TEMP] * num_states for _ in range(N + 1)]
        bypass_state = [[False] * num_states for _ in range(N + 1)]

        dp[0][start_idx] = 0.0
        elec_temp[0][start_idx] = t_elec_start
        bypass_state[0][start_idx] = bypass_start

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

                T_gas_prev = GRID_MIN_TEMP + prev_idx * 0.5
                T_elec_prev = elec_temp[h - 1][prev_idx]
                T_bypass_prev = bypass_state[h - 1][prev_idx]

                R_gas = get_lut_rate(standby_losses.get("gas", {}), T_gas_prev)
                R_elec = get_lut_rate(standby_losses.get("elec", {}), T_elec_prev)
                
                T_gas_cooled = max(GRID_MIN_TEMP, T_gas_prev - R_gas)
                T_elec_cooled = max(GRID_MIN_TEMP, T_elec_prev - R_elec)
                total_vol = vol_gas + vol_elec

                for mode in ["IDLE", "GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]:
                    if mode in ("GAS", "GAS_PUMP") and vol_gas <= 0.0:
                        continue
                    if mode in ("ELEC", "ELEC_PUMP") and (vol_elec <= 0.0 or not allow_elec):
                        continue

                    if mode == "IDLE":
                        T_bypass_end_val = T_bypass_prev
                        T_gas_end_val = T_gas_cooled
                        T_elec_end_val = T_elec_cooled
                        
                        if not T_bypass_end_val:
                            T_active = T_gas_end_val
                        else:
                            T_active = T_elec_end_val
                            
                        t_max_mode = t_max
                        curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                        if 0 <= curr_idx < num_states:
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            # FIX: Use GRID_MIN_TEMP instead of t_min_limit for inactive gas boiler T_curr limit
                            if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                if T_active >= t_min_limit:
                                    cost = 0.0
                                    energy = 0.0
                                    penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                                    if tariff <= 0.0:
                                        reward = temp_reward * (max(0.0, T_gas_end_val - t_min) + max(0.0, T_elec_end_val - t_min))
                                    else:
                                        reward = temp_reward * max(0.0, T_active - t_min)
                                    new_cost = dp[h - 1][prev_idx] + cost + penalty - reward
                                    if new_cost < dp[h][curr_idx]:
                                        dp[h][curr_idx] = new_cost
                                        prev_state[h][curr_idx] = prev_idx
                                        prev_mode[h][curr_idx] = mode
                                        prev_cost[h][curr_idx] = cost
                                        prev_energy[h][curr_idx] = energy
                                        elec_temp[h][curr_idx] = T_elec_end_val
                                        bypass_state[h][curr_idx] = T_bypass_end_val

                    elif mode == "GAS":
                        T_elec_end_val = T_elec_cooled
                        T_bypass_end_val = False
                        t_max_mode = t_max_gas
                        max_rise = 40.0

                        for curr_idx in range(num_states):
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
                            if T_curr < t_min_limit or T_curr > t_max_mode:
                                continue
                            if T_active < t_min_limit:
                                continue
                            delta_T = T_curr - T_gas_cooled
                            if delta_T < -0.25 or delta_T > max_rise:
                                continue
                            heat_rise = max(0.0, delta_T)
                            gas_qty = heat_rise / eff_gas_only if eff_gas_only > 0.0 else 0.0
                            cost = gas_qty * gas_cost_m3
                            energy = gas_qty
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_elec_end_val - t_min))
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward
                            if new_cost < dp[h][curr_idx]:
                                dp[h][curr_idx] = new_cost
                                prev_state[h][curr_idx] = prev_idx
                                prev_mode[h][curr_idx] = mode
                                prev_cost[h][curr_idx] = cost
                                prev_energy[h][curr_idx] = energy
                                elec_temp[h][curr_idx] = T_elec_end_val
                                bypass_state[h][curr_idx] = T_bypass_end_val

                    elif mode == "GAS_PUMP":
                        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol if total_vol > 0.0 else (T_gas_cooled + T_elec_cooled) / 2.0
                        T_bypass_end_val = True
                        t_max_mode = t_max_gas
                        max_rise = 40.0

                        for curr_idx in range(num_states):
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
                            if T_curr < t_min_limit or T_curr > t_max_mode:
                                continue
                            if T_active < t_min_limit:
                                continue
                            delta_T = T_curr - T_mixed
                            if delta_T < -0.25:
                                continue
                            heat_rise = max(0.0, delta_T)
                            gas_qty = heat_rise / eff_gas_pump if eff_gas_pump > 0.0 else 0.0
                            cost = gas_qty * gas_cost_m3
                            energy = gas_qty
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_curr - t_min))
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward
                            if new_cost < dp[h][curr_idx]:
                                dp[h][curr_idx] = new_cost
                                prev_state[h][curr_idx] = prev_idx
                                prev_mode[h][curr_idx] = mode
                                prev_cost[h][curr_idx] = cost
                                prev_energy[h][curr_idx] = energy
                                elec_temp[h][curr_idx] = T_curr
                                bypass_state[h][curr_idx] = T_bypass_end_val

                    elif mode == "ELEC":
                        power_kw = cal_data.get("elec_only", {}).get("heater_power_kw", 2.5)
                        max_rise_elec = power_kw * eff_elec_only
                        T_elec_end_val = min(t_max_elec, T_elec_cooled + max_rise_elec)
                        T_gas_end_val = T_gas_cooled
                        
                        for T_bypass_end_val in (True, False):
                            if not T_bypass_end_val:
                                T_active = T_gas_end_val
                            else:
                                T_active = T_elec_end_val
                            
                            t_max_mode = t_max
                            curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                            if 0 <= curr_idx < num_states:
                                T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                                # FIX: Use GRID_MIN_TEMP instead of t_min_limit for inactive gas boiler T_curr limit
                                if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                    if T_active >= t_min_limit:
                                        kwh = max(0.0, T_elec_end_val - T_elec_cooled) / eff_elec_only if eff_elec_only > 0.0 else 0.0
                                        cost = kwh * tariff
                                        energy = kwh
                                        penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                                        if tariff <= 0.0:
                                            reward = temp_reward * (max(0.0, T_gas_end_val - t_min) + max(0.0, T_elec_end_val - t_min))
                                        else:
                                            reward = temp_reward * max(0.0, T_active - t_min)
                                        new_cost = dp[h - 1][prev_idx] + cost + penalty - reward
                                        if new_cost < dp[h][curr_idx]:
                                            dp[h][curr_idx] = new_cost
                                            prev_state[h][curr_idx] = prev_idx
                                            prev_mode[h][curr_idx] = mode
                                            prev_cost[h][curr_idx] = cost
                                            prev_energy[h][curr_idx] = energy
                                            elec_temp[h][curr_idx] = T_elec_end_val
                                            bypass_state[h][curr_idx] = T_bypass_end_val

                    elif mode == "ELEC_PUMP":
                        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol if total_vol > 0.0 else (T_gas_cooled + T_elec_cooled) / 2.0
                        T_bypass_end_val = True
                        power_kw = cal_data.get("elec_with_pump", {}).get("heater_power_kw", 2.5)
                        max_rise = power_kw * eff_elec_pump
                        t_max_mode = t_max_elec

                        for curr_idx in range(num_states):
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
                            if T_curr < t_min_limit or T_curr > t_max_mode:
                                continue
                            if T_active < t_min_limit:
                                continue
                            delta_T = T_curr - T_mixed
                            if delta_T < -0.25:
                                continue
                            heat_rise = max(0.0, delta_T)
                            kwh = heat_rise / eff_elec_pump if eff_elec_pump > 0.0 else 0.0
                            cost = kwh * tariff
                            energy = kwh
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_curr - t_min))
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward
                            if new_cost < dp[h][curr_idx]:
                                dp[h][curr_idx] = new_cost
                                prev_state[h][curr_idx] = prev_idx
                                prev_mode[h][curr_idx] = mode
                                prev_cost[h][curr_idx] = cost
                                prev_energy[h][curr_idx] = energy
                                elec_temp[h][curr_idx] = T_curr
                                bypass_state[h][curr_idx] = T_bypass_end_val

        best_idx = min(range(num_states), key=lambda i: dp[N][i])
        if dp[N][best_idx] != float("inf"):
            path = []
            curr_idx = best_idx
            for h in range(N, 0, -1):
                prev_idx = prev_state[h][curr_idx]
                mode = prev_mode[h][curr_idx]
                cost = prev_cost[h][curr_idx]
                energy = prev_energy[h][curr_idx]

                gas_start = GRID_MIN_TEMP + prev_idx * 0.5
                gas_end = GRID_MIN_TEMP + curr_idx * 0.5
                elec_start = elec_temp[h - 1][prev_idx]
                elec_end = elec_temp[h][curr_idx]
                bypass_end_step = bypass_state[h][curr_idx]

                if mode == "GAS":
                    active_start = gas_start
                    active_end = gas_end
                elif mode in ("GAS_PUMP", "ELEC_PUMP"):
                    mix_start = (gas_start * vol_gas + elec_start * vol_elec) / total_vol if total_vol > 0.0 else (gas_start + elec_start) / 2.0
                    active_start = mix_start
                    active_end = gas_end
                else:
                    if mode == "ELEC":
                        active_start = elec_start
                        active_end = elec_end
                    else:
                        if not bypass_end_step:
                            active_start = gas_start
                            active_end = gas_end
                        else:
                            active_start = elec_start
                            active_end = elec_end

                path.append({
                    "hour_index": h - 1,
                    "mode": mode,
                    "cost": round(cost, 4),
                    "energy": round(energy, 4),
                    "temp_gas_start": round(gas_start, 2),
                    "temp_gas_end": round(gas_end, 2),
                    "temp_elec_start": round(elec_start, 2),
                    "temp_elec_end": round(elec_end, 2),
                    "temp_active_start": round(active_start, 2),
                    "temp_active_end": round(active_end, 2),
                    "temp_start": round(active_start, 2),
                    "temp_end": round(active_end, 2),
                    "bypass": bypass_end_step,
                })
                curr_idx = prev_idx

            path.reverse()
            return "OK", path, {}
    return "FAILED", [], {}

# Run DP from Hour 10 to 23
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

status, schedule, stats = run_boiler_dp_fixed(
    slots[10:],
    41.0, # t_gas_start
    70.0, # t_elec_start
    False, # bypass_start
    t_min,
    t_max_elec,
    t_max_gas,
    vol_elec,
    vol_gas,
    gas_cost_m3,
    cal_data
)

print("Fixed schedule from Hour 10:")
for s in schedule:
    print(f"Hour {s['hour_index']+10:02d}: mode={s['mode']:9s} gas={s['temp_gas_start']}->{s['temp_gas_end']} elec={s['temp_elec_start']}->{s['temp_elec_end']} active={s['temp_active_start']}->{s['temp_active_end']} bypass={str(s['bypass']):5s} cost={s['cost']:.4f} sell_p={sell_prices[s['hour_index']+10]:.5f}")
