"""Boiler DP Engine for EMS."""
import logging
import math
from typing import Any, Dict, List, Tuple
from .const import INVERTER_MODES

_LOGGER = logging.getLogger(__name__)

def get_lut_rate(lut: dict, temp: float) -> float:
    """Find standby cooling rate (°C/h) for the given temperature from LUT.

    Supports both old format (float/int) and new format (dict: {"value": X, "updated_at": Y}).
    """
    if not lut:
        return 0.0

    bracket_top = math.ceil(temp / 5.0) * 5.0
    bracket_bottom = bracket_top - 5.0
    if temp == bracket_top:
        bracket_top += 5.0
        bracket_bottom = bracket_top - 5.0

    key = f"{int(bracket_top)}_{int(bracket_bottom)}"
    raw_rate = lut.get(key)
    if raw_rate is not None:
        rate_val = raw_rate.get("value") if isinstance(raw_rate, dict) else raw_rate
        if rate_val is not None:
            try:
                rate_float = float(rate_val)
                if rate_float > 0:
                    return rate_float
            except (ValueError, TypeError):
                pass

    # Fallback to the closest available non-zero bracket
    best_key = None
    best_delta = float("inf")
    for k, v in lut.items():
        val = v.get("value") if isinstance(v, dict) else v
        if val is None:
            continue
        try:
            val_float = float(val)
            if val_float <= 0:
                continue
        except (ValueError, TypeError):
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
        best_val = lut[best_key]
        rate_val = best_val.get("value") if isinstance(best_val, dict) else best_val
        if rate_val is not None:
            try:
                return float(rate_val)
            except (ValueError, TypeError):
                pass
    return 0.0

def is_hour_in_range(hour: int, start: int, end: int) -> bool:
    """Check if the given hour is within the allowed heating range (inclusive)."""
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end

def run_boiler_dp(
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
    heating_start_hour: int = 0,
    heating_end_hour: int = 23,
    bat_capacity: float = 5.12,
    actual_boiler_today: float = 0.0,
    min_bat_soc: float = 20.0,
    vacation_mode: bool = False,
    battery_cycle_cost: float = 0.0,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Run Dynamic Programming strategy optimizer for hot water boiler.

    Returns (status, schedule_list, stats_dict).
    """
    # 1. Validation
    eff_gas_only = cal_data.get("gas_only", {}).get("efficiency_c_per_m3", 0.0)
    eff_gas_pump = cal_data.get("gas_with_pump", {}).get("efficiency_c_per_m3", 0.0)
    eff_elec_only = cal_data.get("elec_only", {}).get("efficiency_c_per_kwh", 0.0)
    eff_elec_pump = cal_data.get("elec_with_pump", {}).get("efficiency_c_per_kwh", 0.0)

    has_active_boiler = False
    if vol_gas > 0.0:
        if eff_gas_only <= 0.0 or eff_gas_pump <= 0.0:
            _LOGGER.warning("EMS Boiler DP: Missing or invalid gas calibration data. Gas Only: %s, Gas Pump: %s", eff_gas_only, eff_gas_pump)
            return "NO CALIB DATA", [], {}
        has_active_boiler = True
    if vol_elec > 0.0:
        if eff_elec_only <= 0.0 or eff_elec_pump <= 0.0:
            _LOGGER.warning("EMS Boiler DP: Missing or invalid electric calibration data. Elec Only: %s, Elec Pump: %s", eff_elec_only, eff_elec_pump)
            return "NO CALIB DATA", [], {}
        has_active_boiler = True

    if not has_active_boiler:
        _LOGGER.warning("EMS Boiler DP: No active boilers configured or available.")
        return "NO CALIB DATA", [], {}

    standby_losses = cal_data.get("standby_losses", {})

    t_max = max(t_max_elec, t_max_gas)
    if t_min >= t_max:
        _LOGGER.error("EMS Boiler DP: Invalid temperature ranges: T_min (%s) >= T_max (%s)", t_min, t_max)
        return "ERROR", [], {}

    start_h = max(0, min(23, int(round(heating_start_hour))))
    end_h = max(0, min(23, int(round(heating_end_hour))))

    # Define grid max temp to accommodate starting temperatures if they exceed t_max (with safety upper bound 95.0°C)
    t_grid_max = min(95.0, max(t_max, t_gas_start, t_elec_start))

    # Total grid size with lower bound at 20.0°C (water supply temp) to allow unused boiler to cool freely
    GRID_MIN_TEMP = min(20.0, t_min)
    num_states = int(round((t_grid_max - GRID_MIN_TEMP) * 2)) + 1
    
    # Clamp starting gas temp to grid range
    t_gas_start_clamped = max(GRID_MIN_TEMP, min(t_gas_start, t_grid_max))
    start_idx = int(round((t_gas_start_clamped - GRID_MIN_TEMP) * 2))

    N = len(slots)
    if N == 0:
        return "idle", [], {}

    # 2. Curtailed PV Energy Budget Allocation (Arbitrage)
    today_date = slots[0].get("date") if slots else None

    # Calculate maximum export prices during solar hours for today and tomorrow
    max_sell_today = max([float(s.get("sell_price", 0.0) or 0.0) for s in slots if s.get("date") == today_date and float(s.get("pv_kwh", 0.0) or 0.0) > 0.5], default=0.0)
    max_sell_tomorrow = max([float(s.get("sell_price", 0.0) or 0.0) for s in slots if s.get("date") != today_date and float(s.get("pv_kwh", 0.0) or 0.0) > 0.5], default=0.0)
    
    # 2.1 Calculate historical averages budget
    boiler_average_budget_today = 0.0
    boiler_average_budget_tomorrow = 0.0
    for slot in slots:
        planned_boiler = float(slot.get("planned_boiler_kwh", 0.0))
        if slot.get("date") == today_date:
            boiler_average_budget_today += planned_boiler
        else:
            boiler_average_budget_tomorrow += planned_boiler

    # 2.2 Calculate wasted/curtailed PV
    curtailed_pv_today = 0.0
    curtailed_pv_tomorrow = 0.0
    for slot in slots:
        mode_name = slot.get("physical_mode", "idle")
        mode_config = INVERTER_MODES.get(mode_name)
        curtail_active = mode_config.curtail_pv if mode_config else False
        if curtail_active:
            pv_kwh = float(slot.get("pv_kwh", 0.0))
            planned_boiler = float(slot.get("planned_boiler_kwh", 0.0))
            consumption_kwh = float(slot.get("consumption_kwh", 0.0))
            action = slot.get("action", "idle")
            battery_charge = float(slot.get("energy_kwh", 0.0)) if action in ("pv_charge", "grid_charge") else 0.0
            wasted = max(0.0, pv_kwh - consumption_kwh - battery_charge)
            if slot.get("date") == today_date:
                curtailed_pv_today += wasted
            else:
                curtailed_pv_tomorrow += wasted

    # 2.3 Sum 1 and 2 to get total budget (with a 10% safety margin)
    total_pv_budget_today = (boiler_average_budget_today + curtailed_pv_today) * 0.9
    total_pv_budget_tomorrow = (boiler_average_budget_tomorrow + curtailed_pv_tomorrow) * 0.9

    # 2.4 Subtract battery charging deficit if battery doesn't reach 100% SOC before evening
    # Adjust expected_soc by adding back the planned boiler energy to estimate baseline SOC trajectory
    safe_capacity = bat_capacity if bat_capacity > 0.0 else 5.12
    
    # Process today slots
    cum_boiler_today = 0.0
    adjusted_socs_today = []
    max_soc_today_raw = 100.0
    raw_socs_today = [float(slot.get("expected_soc", 50.0)) for slot in slots if slot.get("date") == today_date]
    if raw_socs_today:
        max_soc_today_raw = max(raw_socs_today)
        for slot in slots:
            if slot.get("date") == today_date:
                actual_planned = float(slot.get("actual_planned_boiler_kwh", 0.0))
                cum_boiler_today += actual_planned
                soc = float(slot.get("expected_soc", 50.0))
                adj_soc = soc + (cum_boiler_today / safe_capacity) * 100.0
                adjusted_socs_today.append(min(100.0, adj_soc))
    max_soc_today = max(adjusted_socs_today, default=max_soc_today_raw)
    
    _LOGGER.debug(
        "EMS Boiler DP: Today SOC raw=%.1f%%, adjusted=%.1f%% (accumulated boiler=%.2fkWh)",
        max_soc_today_raw,
        max_soc_today,
        cum_boiler_today
    )

    if max_soc_today < 98.0:
        soc_deficit = 100.0 - max_soc_today
        energy_deficit = safe_capacity * (soc_deficit / 100.0)
        total_pv_budget_today = max(0.0, total_pv_budget_today - energy_deficit)

    # Process tomorrow slots
    cum_boiler_tomorrow = 0.0
    adjusted_socs_tomorrow = []
    max_soc_tomorrow_raw = 100.0
    raw_socs_tomorrow = [float(slot.get("expected_soc", 50.0)) for slot in slots if slot.get("date") != today_date]
    if raw_socs_tomorrow:
        max_soc_tomorrow_raw = max(raw_socs_tomorrow)
        for slot in slots:
            if slot.get("date") != today_date:
                actual_planned = float(slot.get("actual_planned_boiler_kwh", 0.0))
                cum_boiler_tomorrow += actual_planned
                soc = float(slot.get("expected_soc", 50.0))
                adj_soc = soc + (cum_boiler_tomorrow / safe_capacity) * 100.0
                adjusted_socs_tomorrow.append(min(100.0, adj_soc))
    max_soc_tomorrow = max(adjusted_socs_tomorrow, default=max_soc_tomorrow_raw)

    _LOGGER.debug(
        "EMS Boiler DP: Tomorrow SOC raw=%.1f%%, adjusted=%.1f%% (accumulated boiler=%.2fkWh)",
        max_soc_tomorrow_raw,
        max_soc_tomorrow,
        cum_boiler_tomorrow
    )

    if max_soc_tomorrow < 98.0:
        soc_deficit = 100.0 - max_soc_tomorrow
        energy_deficit = safe_capacity * (soc_deficit / 100.0)
        total_pv_budget_tomorrow = max(0.0, total_pv_budget_tomorrow - energy_deficit)

    allocated_free_slots = set()
    boiler_hourly_draw = cal_data.get("elec_with_pump", {}).get("heater_power_kw") or 2.5

    # Распределение лимита для сегодняшнего дня
    if total_pv_budget_today >= 0.5:
        eligible_today = []
        for idx, slot in enumerate(slots):
            if slot.get("date") != today_date:
                continue
            mode_name = slot.get("physical_mode", "idle")
            mode_config = INVERTER_MODES.get(mode_name)
            allow_boiler = getattr(mode_config, "allow_boiler", False) if mode_config else False
            allow_elec_static = allow_boiler or mode_name in ("sale_pv_bat", "sale_pv_no_bat")
            pv_kwh = float(slot.get("pv_kwh", 0.0) or 0.0)
            consumption_kwh = float(slot.get("consumption_kwh", 0.0) or 0.0)
            
            is_curtailed = getattr(mode_config, "curtail_pv", False) if mode_config else False
            has_full_charge = max_soc_today >= 90.0
            is_eligible_hour = is_curtailed or (has_full_charge and pv_kwh > consumption_kwh)
            if allow_elec_static and pv_kwh > 0.5 and is_eligible_hour:
                soc = float(slot.get("expected_soc", 50.0) or 50.0)
                limit_soc = getattr(mode_config, "calibration_limit_soc", 90.0) or 90.0
                already_free = soc >= limit_soc
                if not already_free:
                    eligible_today.append((idx, slot))

        eligible_today.sort(
            key=lambda item: (
                -float(item[1].get("pv_kwh", 0.0) or 0.0),
                float(item[1].get("buy_price", 0.0) or 0.0),
                item[0]
            )
        )
        rem_today = total_pv_budget_today
        for idx, slot in eligible_today:
            if rem_today <= 0.0:
                break
            allocated_free_slots.add(idx)
            rem_today -= boiler_hourly_draw

    # Распределение лимита для завтрашнего дня
    if total_pv_budget_tomorrow >= 0.5:
        eligible_tomorrow = []
        for idx, slot in enumerate(slots):
            if slot.get("date") == today_date:
                continue
            mode_name = slot.get("physical_mode", "idle")
            mode_config = INVERTER_MODES.get(mode_name)
            allow_boiler = getattr(mode_config, "allow_boiler", False) if mode_config else False
            allow_elec_static = allow_boiler or mode_name in ("sale_pv_bat", "sale_pv_no_bat")
            pv_kwh = float(slot.get("pv_kwh", 0.0) or 0.0)
            consumption_kwh = float(slot.get("consumption_kwh", 0.0) or 0.0)
            
            is_curtailed = getattr(mode_config, "curtail_pv", False) if mode_config else False
            has_full_charge_tomorrow = max_soc_tomorrow >= 90.0
            is_eligible_hour = is_curtailed or (has_full_charge_tomorrow and pv_kwh > consumption_kwh)
            if allow_elec_static and pv_kwh > 0.5 and is_eligible_hour:
                soc = float(slot.get("expected_soc", 50.0) or 50.0)
                limit_soc = getattr(mode_config, "calibration_limit_soc", 90.0) or 90.0
                already_free = soc >= limit_soc
                if not already_free:
                    eligible_tomorrow.append((idx, slot))

        eligible_tomorrow.sort(
            key=lambda item: (
                -float(item[1].get("pv_kwh", 0.0) or 0.0),
                float(item[1].get("buy_price", 0.0) or 0.0),
                item[0]
            )
        )
        rem_tomorrow = total_pv_budget_tomorrow
        for idx, slot in eligible_tomorrow:
            if rem_tomorrow <= 0.0:
                break
            allocated_free_slots.add(idx)
            rem_tomorrow -= boiler_hourly_draw

    # Рассчитываем раздельные динамические лимиты температуры
    dynamic_t_max_elec_today_pump = t_max_elec
    dynamic_t_max_elec_today_only = t_max_elec
    total_budget_for_limit = total_pv_budget_today + actual_boiler_today
    if total_budget_for_limit > 0.0:
        eff_pump_val = eff_elec_pump if eff_elec_pump > 0.0 else 3.35
        budget_rise_pump = total_budget_for_limit * eff_pump_val
        dynamic_t_max_elec_today_pump = min(t_max_elec, max(t_min, t_min + budget_rise_pump))

        eff_only_val = eff_elec_only if eff_elec_only > 0.0 else 20.4
        budget_rise_only = total_budget_for_limit * eff_only_val
        dynamic_t_max_elec_today_only = min(t_max_elec, max(t_min, t_min + budget_rise_only))

    dynamic_t_max_elec_tomorrow_pump = t_max_elec
    dynamic_t_max_elec_tomorrow_only = t_max_elec
    if total_pv_budget_tomorrow > 0.0:
        eff_pump_val = eff_elec_pump if eff_elec_pump > 0.0 else 3.35
        budget_rise_pump = total_pv_budget_tomorrow * eff_pump_val
        dynamic_t_max_elec_tomorrow_pump = min(t_max_elec, max(t_min, t_min + budget_rise_pump))

        eff_only_val = eff_elec_only if eff_elec_only > 0.0 else 20.4
        budget_rise_only = total_pv_budget_tomorrow * eff_only_val
        dynamic_t_max_elec_tomorrow_only = min(t_max_elec, max(t_min, t_min + budget_rise_only))

    # 2.5 Precalculate effective electricity tariffs for all slots to prevent inverter mode oscillations
    cum_boiler_today = 0.0
    cum_boiler_tomorrow = 0.0
    for idx, slot in enumerate(slots):
        buy_price = float(slot.get("buy_price", 0.0))
        sell_price = float(slot.get("sell_price", 0.0))
        mode_name = slot.get("physical_mode", "idle")
        soc = float(slot.get("expected_soc", 50.0))
        is_tomorrow = slot.get("date") != today_date
        max_solar_sell = max_sell_tomorrow if is_tomorrow else max_sell_today
        
        # Reconstruct baseline SOC trajectory by adding back planned boiler energy (decouples feedback loop)
        actual_planned = float(slot.get("actual_planned_boiler_kwh", 0.0))
        safe_capacity = bat_capacity if bat_capacity > 0.0 else 5.12
        
        if slot.get("date") == today_date:
            cum_boiler_today += actual_planned
            adjusted_soc = soc + (cum_boiler_today / safe_capacity) * 100.0
        else:
            cum_boiler_tomorrow += actual_planned
            adjusted_soc = soc + (cum_boiler_tomorrow / safe_capacity) * 100.0
            
        adjusted_soc = min(100.0, max(0.0, adjusted_soc))
        mode_config = INVERTER_MODES.get(mode_name)
        grid_available = getattr(mode_config, "is_grid_bypass", False) if mode_config else False

        if mode_name == "buy":
            eff_tariff = buy_price
        elif idx in allocated_free_slots:
            eff_tariff = 0.0
        elif mode_config and mode_config.curtail_pv:
            limit_soc = getattr(mode_config, "calibration_limit_soc", 90.0) or 90.0
            if adjusted_soc >= limit_soc:
                # Battery is full enough — use curtailed PV for free
                eff_tariff = 0.0
            elif grid_available:
                # Grid is accessible (e.g. buy mode logic) — charge at buy_price
                eff_tariff = buy_price
            else:
                # No grid access — only local energy (PV surplus + battery above min_soc)
                pv_kwh = float(slot.get("pv_kwh", 0.0))
                consumption_kwh = float(slot.get("consumption_kwh", 0.0))
                pv_surplus = max(0.0, pv_kwh - consumption_kwh)
                discharge_allowed = getattr(mode_config, "discharge_to_house", False)
                bat_avail = max(0.0, (adjusted_soc - min_bat_soc) / 100.0 * safe_capacity) if discharge_allowed else 0.0
                available_local = pv_surplus + bat_avail
                boiler_power = max(0.1, float(boiler_hourly_draw))
                if available_local <= 0.0:
                    # No local energy at all — heating physically impossible
                    eff_tariff = 9999.0
                else:
                    solar_covered = min(boiler_power, pv_surplus)
                    remaining_power = boiler_power - solar_covered
                    battery_covered = min(remaining_power, bat_avail)
                    uncovered = remaining_power - battery_covered
                    is_solar_mode = mode_name in ("sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat")
                    if is_solar_mode or pv_surplus > 0.0:
                        battery_valuation = max_solar_sell + battery_cycle_cost
                    else:
                        battery_valuation = max(buy_price, sell_price) + battery_cycle_cost
                    eff_tariff = (solar_covered * sell_price + battery_covered * battery_valuation + uncovered * 9999.0) / boiler_power
        else:
            # Calculate effective tariff considering local energy coverage using baseline adjusted_soc
            pv_kwh = float(slot.get("pv_kwh", 0.0))
            consumption_kwh = float(slot.get("consumption_kwh", 0.0))
            pv_surplus = max(0.0, pv_kwh - consumption_kwh)

            # Battery energy stored above min_bat_soc using adjusted_soc, only if discharge to house is allowed
            discharge_allowed = getattr(mode_config, "discharge_to_house", False) if mode_config else False
            if discharge_allowed:
                battery_energy_above_min = max(0.0, (adjusted_soc - min_bat_soc) / 100.0 * safe_capacity)
            else:
                battery_energy_above_min = 0.0

            available_local_energy = pv_surplus + battery_energy_above_min
            boiler_power = max(0.1, float(boiler_hourly_draw))

            solar_covered = min(boiler_power, pv_surplus)
            remaining_power = boiler_power - solar_covered
            battery_covered = min(remaining_power, battery_energy_above_min)
            grid_import_needed = remaining_power - battery_covered

            # If grid is not accessible in this mode — treat grid import as prohibitively expensive
            grid_import_price = buy_price if grid_available else 9999.0
            is_solar_mode = mode_name in ("sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat")
            if is_solar_mode or pv_surplus > 0.0:
                battery_valuation = max_solar_sell + battery_cycle_cost
            else:
                battery_valuation = max(buy_price, sell_price) + battery_cycle_cost
            eff_tariff = (solar_covered * sell_price + battery_covered * battery_valuation + grid_import_needed * grid_import_price) / boiler_power

        slot["effective_tariff"] = eff_tariff


    # Run DP with relaxed constraint fallback
    best_path = None
    relaxed_used = False

    for relax in [False, True]:
        t_min_limit = t_min if not relax else 20.0
        
        # dp[h][state_idx] -> minimum cost to reach state_idx at end of slot h (1-indexed)
        dp = [[float("inf")] * num_states for _ in range(N + 1)]
        prev_state = [[-1] * num_states for _ in range(N + 1)]
        prev_mode = [["IDLE"] * num_states for _ in range(N + 1)]
        prev_cost = [[0.0] * num_states for _ in range(N + 1)]
        prev_energy = [[0.0] * num_states for _ in range(N + 1)]
        
        # elec_temp[h][state_idx] -> temperature of electric boiler at end of slot h
        elec_temp = [[GRID_MIN_TEMP] * num_states for _ in range(N + 1)]
        # bypass_state[h][state_idx] -> True if bypass is open (serial) at end of slot h
        bypass_state = [[False] * num_states for _ in range(N + 1)]

        # Initial state (hour 0)
        dp[0][start_idx] = 0.0
        elec_temp[0][start_idx] = t_elec_start
        bypass_state[0][start_idx] = bypass_start

        for h in range(1, N + 1):
            slot = slots[h - 1]
            prev_planned_kwh = float(slot.get("actual_planned_boiler_kwh", 0.0) or 0.0)
            was_elec = prev_planned_kwh > 0.05
            buy_price = slot.get("buy_price", 0.0)
            sell_price = slot.get("sell_price", 0.0)
            mode_name = slot.get("physical_mode", "idle")
            soc = slot.get("expected_soc", 50.0)

            # Retrieve Inverter mode configurations
            mode_config = INVERTER_MODES.get(mode_name)
            
            # Electricity pricing tariff logic (precalculated effective tariff)
            tariff = slot.get("effective_tariff", sell_price)

            # Electric heating allowance check
            allow_boiler = getattr(mode_config, "allow_boiler", False) if mode_config else False
            allow_elec = allow_boiler or mode_name in ("sale_pv_bat", "sale_pv_no_bat")

            # Cost per 1°C rise comparisons for ELEC and ELEC_PUMP
            c_per_gas = (1.0 / eff_gas_only if eff_gas_only > 0.0 else 0.0) * gas_cost_m3
            c_per_elec = (1.0 / eff_elec_only if eff_elec_only > 0.0 else 0.0) * tariff
            c_per_gas_pump = (1.0 / eff_gas_pump if eff_gas_pump > 0.0 else 0.0) * gas_cost_m3 + (0.1 / eff_gas_pump if eff_gas_pump > 0.0 else 0.0) * tariff
            c_per_elec_pump = (1.1 / eff_elec_pump if eff_elec_pump > 0.0 else 0.0) * tariff

            is_solar_excess = (h - 1) in allocated_free_slots or tariff <= 0.0
            
            allow_elec_mode = allow_elec and (is_solar_excess or (c_per_elec < c_per_gas))
            allow_elec_pump_mode = allow_elec and (is_solar_excess or (c_per_elec_pump < c_per_gas_pump))

            for prev_idx in range(num_states):
                if dp[h - 1][prev_idx] == float("inf"):
                    continue

                T_gas_prev = GRID_MIN_TEMP + prev_idx * 0.5
                T_elec_prev = elec_temp[h - 1][prev_idx]
                T_bypass_prev = bypass_state[h - 1][prev_idx]

                # Standby cooling
                R_gas = get_lut_rate(standby_losses.get("gas", {}), T_gas_prev)
                R_elec = get_lut_rate(standby_losses.get("elec", {}), T_elec_prev)
                
                T_gas_cooled = max(GRID_MIN_TEMP, T_gas_prev - R_gas)
                T_elec_cooled = max(GRID_MIN_TEMP, T_elec_prev - R_elec)
                total_vol = vol_gas + vol_elec

                # Iterate modes
                slot_hour = slot.get("hour", 0)
                allowed_heating = is_hour_in_range(slot_hour, start_h, end_h)

                for mode in ["IDLE", "PUMP_ONLY", "GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]:
                    if mode != "IDLE" and not allowed_heating:
                        continue
                    if mode in ("GAS", "GAS_PUMP") and T_elec_prev >= t_min:
                        continue
                    if mode in ("GAS", "GAS_PUMP") and vol_gas <= 0.0:
                        continue
                    if mode == "ELEC" and (vol_elec <= 0.0 or not allow_elec_mode):
                        continue
                    if mode == "ELEC_PUMP" and (vol_elec <= 0.0 or not allow_elec_pump_mode):
                        continue

                    # Mode-specific transitions
                    if mode == "IDLE":
                        T_bypass_end_val = (T_elec_prev >= t_min)
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
                            if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                if not allowed_heating or T_active >= t_min_limit:
                                    cost = 0.0
                                    energy = 0.0
                                    
                                    penalty = 1000.0 * (t_min - T_active) if (relax and allowed_heating and T_active < t_min) else 0.0
                                    if tariff <= 0.0:
                                        reward = temp_reward * (max(0.0, T_gas_end_val - t_min) + max(0.0, T_elec_end_val - t_min))
                                    else:
                                        reward = temp_reward * max(0.0, T_active - t_min)
                                    new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if not was_elec else 0.0)
                                    if new_cost < dp[h][curr_idx]:
                                        dp[h][curr_idx] = new_cost
                                        prev_state[h][curr_idx] = prev_idx
                                        prev_mode[h][curr_idx] = mode
                                        prev_cost[h][curr_idx] = cost
                                        prev_energy[h][curr_idx] = energy
                                        elec_temp[h][curr_idx] = T_elec_end_val
                                        bypass_state[h][curr_idx] = T_bypass_end_val

                    elif mode == "PUMP_ONLY":
                        if T_elec_prev < t_min:
                            continue
                        if abs(T_gas_cooled - T_elec_cooled) < 5.0:
                            continue
                            
                        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol if total_vol > 0.0 else (T_gas_cooled + T_elec_cooled) / 2.0
                        T_bypass_end_val = True
                        t_max_mode = t_max

                        curr_idx = int(round((T_mixed - GRID_MIN_TEMP) * 2))
                        if 0 <= curr_idx < num_states:
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
                            if T_active >= t_min_limit and T_active <= t_max_mode:
                                cost = 0.1 * tariff
                                energy = 0.1
                                
                                penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                                if tariff <= 0.0:
                                    reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_curr - t_min))
                                else:
                                    reward = temp_reward * max(0.0, T_active - t_min)
                                new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if not was_elec else 0.0)
                                if new_cost < dp[h][curr_idx]:
                                    dp[h][curr_idx] = new_cost
                                    prev_state[h][curr_idx] = prev_idx
                                    prev_mode[h][curr_idx] = mode
                                    prev_cost[h][curr_idx] = cost
                                    prev_energy[h][curr_idx] = energy
                                    elec_temp[h][curr_idx] = T_curr
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
                            if T_curr < T_gas_prev:
                                # Active heating must not result in temperature drop
                                continue
                            if T_active < t_min_limit:
                                continue
                            
                            delta_T = T_curr - T_gas_cooled
                            if delta_T <= 0.0 or delta_T > max_rise:
                                continue
                            
                            heat_rise = delta_T
                            gas_qty = heat_rise / eff_gas_only if eff_gas_only > 0.0 else 0.0
                            if gas_qty <= 0.0:
                                continue
                            cost = gas_qty * gas_cost_m3
                            energy = gas_qty
                            
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_elec_end_val - t_min))
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if not was_elec else 0.0)
                            if new_cost < dp[h][curr_idx]:
                                dp[h][curr_idx] = new_cost
                                prev_state[h][curr_idx] = prev_idx
                                prev_mode[h][curr_idx] = mode
                                prev_cost[h][curr_idx] = cost
                                prev_energy[h][curr_idx] = energy
                                elec_temp[h][curr_idx] = T_elec_end_val
                                bypass_state[h][curr_idx] = T_bypass_end_val
 
                    elif mode == "GAS_PUMP":
                        if T_elec_prev < t_min:
                            continue
                        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol if total_vol > 0.0 else (T_gas_cooled + T_elec_cooled) / 2.0
                        T_bypass_end_val = True
                        t_max_mode = t_max_gas
                        max_rise = 40.0
 
                        for curr_idx in range(num_states):
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
 
                            if T_curr < t_min_limit or T_curr > t_max_mode:
                                continue
                            if T_curr < T_mixed:
                                # Active heating must not result in temperature drop
                                continue
                            if T_active < t_min_limit:
                                continue
                            
                            delta_T = T_curr - T_mixed
                            if delta_T <= 0.0 or delta_T > max_rise:
                                continue
                            
                            heat_rise = delta_T
                            gas_qty = heat_rise / eff_gas_pump if eff_gas_pump > 0.0 else 0.0
                            if gas_qty <= 0.0:
                                continue
                            cost = gas_qty * gas_cost_m3 + 0.1 * tariff
                            energy = gas_qty
                            
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_curr - t_min))
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if not was_elec else 0.0)
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
                        
                        is_solar_slot = (h - 1) in allocated_free_slots or (mode_config and mode_config.curtail_pv)
                        is_today_slot = slot.get("date") == today_date
                        current_t_max_elec = (dynamic_t_max_elec_today_only if is_today_slot else dynamic_t_max_elec_tomorrow_only) if is_solar_slot else t_max_elec
                        
                        T_elec_end_val = min(current_t_max_elec, T_elec_cooled + max_rise_elec)
                        T_gas_end_val = T_gas_cooled
                        
                        for T_bypass_end_val in [T_elec_end_val >= t_min]:
                            if not T_bypass_end_val:
                                T_active = T_gas_end_val
                            else:
                                T_active = T_elec_end_val
                            
                            t_max_mode = t_max
                            curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                            if 0 <= curr_idx < num_states:
                                T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                                if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                    if T_elec_end_val < T_elec_prev:
                                        # Active heating must not result in temperature drop
                                        continue
                                    if T_active >= t_min_limit:
                                        delta_T = T_elec_end_val - T_elec_cooled
                                        if delta_T <= 0.0:
                                            continue
                                        kwh = delta_T / eff_elec_only if eff_elec_only > 0.0 else 0.0
                                        if kwh <= 0.0:
                                            continue
                                        cost = kwh * tariff
                                        energy = kwh
                                        
                                        penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                                        if tariff <= 0.0:
                                            reward = temp_reward * (max(0.0, T_gas_end_val - t_min) + max(0.0, T_elec_end_val - t_min)) + 0.01 * kwh
                                        else:
                                            reward = temp_reward * max(0.0, T_active - t_min)
                                        new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if was_elec else 0.0)
                                        if new_cost < dp[h][curr_idx]:
                                            dp[h][curr_idx] = new_cost
                                            prev_state[h][curr_idx] = prev_idx
                                            prev_mode[h][curr_idx] = mode
                                            prev_cost[h][curr_idx] = cost
                                            prev_energy[h][curr_idx] = energy
                                            elec_temp[h][curr_idx] = T_elec_end_val
                                            bypass_state[h][curr_idx] = T_bypass_end_val
 
                    elif mode == "ELEC_PUMP":
                        if T_elec_prev < t_min:
                            continue
                        T_mixed = (T_gas_cooled * vol_gas + T_elec_cooled * vol_elec) / total_vol if total_vol > 0.0 else (T_gas_cooled + T_elec_cooled) / 2.0
                        T_bypass_end_val = True
                        power_kw = cal_data.get("elec_with_pump", {}).get("heater_power_kw") or 2.5
                        eff_elec_pump_val = eff_elec_pump if eff_elec_pump > 0.0 else 3.35
                        max_rise = max(1.0, power_kw * eff_elec_pump_val)
                        
                        is_solar_slot = (h - 1) in allocated_free_slots or (mode_config and mode_config.curtail_pv)
                        is_today_slot = slot.get("date") == today_date
                        current_t_max_elec = (dynamic_t_max_elec_today_pump if is_today_slot else dynamic_t_max_elec_tomorrow_pump) if is_solar_slot else t_max_elec
                        
                        t_max_mode = current_t_max_elec
 
                        for curr_idx in range(num_states):
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            T_active = T_curr
 
                            if T_curr < t_min_limit or T_curr > t_max_mode:
                                continue
                            if T_curr < T_mixed:
                                # Active heating must not result in temperature drop
                                continue
                            if T_active < t_min_limit:
                                continue
                            
                            delta_T = T_curr - T_mixed
                            if delta_T <= 0.0 or delta_T > max_rise:
                                continue
                            
                            heat_rise = delta_T
                            kwh = heat_rise / eff_elec_pump if eff_elec_pump > 0.0 else 0.0
                            if kwh <= 0.0:
                                continue
                            cost = (kwh + 0.1) * tariff
                            energy = kwh + 0.1
                            
                            penalty = 1000.0 * (t_min - T_active) if (relax and T_active < t_min) else 0.0
                            if tariff <= 0.0:
                                reward = temp_reward * (max(0.0, T_curr - t_min) + max(0.0, T_curr - t_min)) + 0.01 * kwh
                            else:
                                reward = temp_reward * max(0.0, T_active - t_min)
                            new_cost = dp[h - 1][prev_idx] + cost + penalty - reward - (0.05 if was_elec else 0.0)
                            if new_cost < dp[h][curr_idx]:
                                dp[h][curr_idx] = new_cost
                                prev_state[h][curr_idx] = prev_idx
                                prev_mode[h][curr_idx] = mode
                                prev_cost[h][curr_idx] = cost
                                prev_energy[h][curr_idx] = energy
                                elec_temp[h][curr_idx] = T_curr
                                bypass_state[h][curr_idx] = T_bypass_end_val

        # Backtrack if path found
        # Calculate terminal rewards at hour N to solve finite-horizon boundary effects
        terminal_rewards = [0.0] * num_states
        if not vacation_mode and N > 0:
            sell_price_final = float(slots[-1].get("sell_price", 0.0))
            for i in range(num_states):
                t_gas_final = GRID_MIN_TEMP + i * 0.5
                t_elec_final = elec_temp[N][i]
                
                reward_elec = 0.0
                if vol_elec > 0.0 and eff_elec_only > 0.0:
                    reward_elec = max(0.0, t_elec_final - t_min) * (sell_price_final / eff_elec_only) * 0.85
                    
                reward_gas = 0.0
                if vol_gas > 0.0 and eff_gas_only > 0.0:
                    reward_gas = max(0.0, t_gas_final - t_min) * (gas_cost_m3 / eff_gas_only) * 0.85
                    
                terminal_rewards[i] = reward_elec + reward_gas

        best_idx = min(range(num_states), key=lambda i: dp[N][i] - terminal_rewards[i])
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
                elif mode in ("GAS_PUMP", "ELEC_PUMP", "PUMP_ONLY"):
                    mix_start = (gas_start * vol_gas + elec_start * vol_elec) / total_vol if total_vol > 0.0 else (gas_start + elec_start) / 2.0
                    active_start = mix_start
                    active_end = gas_end
                else: # IDLE, ELEC
                    if mode == "ELEC":
                        active_start = elec_start
                        active_end = elec_end
                    else: # IDLE
                        if not bypass_end_step:
                            active_start = gas_start
                            active_end = gas_end
                        else:
                            active_start = elec_start
                            active_end = elec_end

                total_vol = vol_gas + vol_elec
                if total_vol > 0.0:
                    sys_start = (gas_start * vol_gas + elec_start * vol_elec) / total_vol
                    sys_end = (gas_end * vol_gas + elec_end * vol_elec) / total_vol
                else:
                    sys_start = (gas_start + elec_start) / 2.0
                    sys_end = (gas_end + elec_end) / 2.0

                dhw_start = elec_start if bypass_end_step else gas_start
                dhw_end = elec_end if bypass_end_step else gas_end

                flow_start = min(t_min, dhw_start)
                flow_end = min(t_min, dhw_end)

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
                    "temp_sys_start": round(sys_start, 2),
                    "temp_sys_end": round(sys_end, 2),
                    "temp_dhw_start": round(dhw_start, 2),
                    "temp_dhw_end": round(dhw_end, 2),
                    "temp_flow_start": round(flow_start, 2),
                    "temp_flow_end": round(flow_end, 2),
                    "temp_start": round(active_start, 2),
                    "temp_end": round(active_end, 2),
                    "bypass": bypass_end_step,
                })
                curr_idx = prev_idx

            path.reverse()
            best_path = path
            relaxed_used = relax
            break

    if best_path is None:
        _LOGGER.error("EMS Boiler DP: Failed to find any feasible schedule path.")
        return "NO PATH", [], {}

    # Build final schedule details with costs per 1°C rise for each mode
    schedule = []
    total_cost = 0.0

    for idx, step in enumerate(best_path):
        slot = slots[idx]
        buy_price = slot.get("buy_price", 0.0)
        sell_price = slot.get("sell_price", 0.0)
        mode_name = slot.get("physical_mode", "idle")
        soc = slot.get("expected_soc", 50.0)

        # Retrieve Inverter mode configurations
        mode_config = INVERTER_MODES.get(mode_name)
        tariff = slot.get("effective_tariff", sell_price)

        # Cost per 1°C rise calculations for each mode
        c_per_gas = (1.0 / eff_gas_only if eff_gas_only > 0.0 else 0.0) * gas_cost_m3
        c_per_gas_pump = (1.0 / eff_gas_pump if eff_gas_pump > 0.0 else 0.0) * gas_cost_m3 + (0.1 / eff_gas_pump if eff_gas_pump > 0.0 else 0.0) * tariff
        c_per_elec = (1.0 / eff_elec_only if eff_elec_only > 0.0 else 0.0) * tariff
        c_per_elec_pump = (1.1 / eff_elec_pump if eff_elec_pump > 0.0 else 0.0) * tariff

        # Determine electric heating allowance
        allow_boiler = getattr(mode_config, "allow_boiler", False) if mode_config else False
        allow_elec = allow_boiler or mode_name in ("sale_pv_bat", "sale_pv_no_bat")

        schedule.append({
            "date": slot.get("date"),
            "hour": slot.get("hour"),
            "mode": step["mode"],
            "temp_start": step["temp_start"],
            "temp_end": step["temp_end"],
            "temp_gas_start": step["temp_gas_start"],
            "temp_gas_end": step["temp_gas_end"],
            "temp_elec_start": step["temp_elec_start"],
            "temp_elec_end": step["temp_elec_end"],
            "temp_active_start": step["temp_active_start"],
            "temp_active_end": step["temp_active_end"],
            "temp_sys_start": step.get("temp_sys_start"),
            "temp_sys_end": step.get("temp_sys_end"),
            "temp_dhw_start": step.get("temp_dhw_start"),
            "temp_dhw_end": step.get("temp_dhw_end"),
            "temp_flow_start": step.get("temp_flow_start"),
            "temp_flow_end": step.get("temp_flow_end"),
            "bypass": step["bypass"],
            "cost": step["cost"],
            "energy": step["energy"],
            "cost_per_c_gas": round(c_per_gas, 4),
            "cost_per_c_gas_pump": round(c_per_gas_pump, 4),
            "cost_per_c_elec": round(c_per_elec, 4) if allow_elec else None,
            "cost_per_c_elec_pump": round(c_per_elec_pump, 4) if allow_elec else None,
        })
        total_cost += step["cost"]

    # Рассчитываем плановое потребление электроэнергии бойлером сегодня
    today_planned_elec = 0.0
    if best_path and today_date:
        for step in best_path:
            if step.get("date") == today_date and step.get("mode") in ("ELEC", "ELEC_PUMP"):
                today_planned_elec += step.get("energy", 0.0)

    remaining_pv_today = max(0.0, curtailed_pv_today - today_planned_elec)

    # Final stats
    stats = {
        "horizon_hours": N,
        "start_temp": best_path[0]["temp_active_start"] if best_path else t_gas_start_clamped,
        "end_temp": best_path[-1]["temp_active_end"] if best_path else t_gas_start_clamped,
        "total_cost": round(total_cost, 4),
        "relaxed_constraint_used": relaxed_used,
        "curtailed_pv_budget": round(curtailed_pv_today + curtailed_pv_tomorrow, 2),
        "curtailed_pv_today": round(curtailed_pv_today, 2),
        "curtailed_pv_tomorrow": round(curtailed_pv_tomorrow, 2),
        "boiler_average_budget_today": round(boiler_average_budget_today, 2),
        "boiler_average_budget_tomorrow": round(boiler_average_budget_tomorrow, 2),
        "total_pv_budget_today": round(total_pv_budget_today + actual_boiler_today, 2),
        "boiler_used_today": round(actual_boiler_today, 2),
        "remaining_pv_today": round(total_pv_budget_today, 2),
    }

    current_action = best_path[0]["mode"] if best_path else "IDLE"
    return current_action, schedule, stats
