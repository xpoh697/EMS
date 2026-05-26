"""Dynamic Programming engine for EMS."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Action type constants
ACT_SOL = 0
ACT_DIS = 1
ACT_PV_CHARGE = 2
ACT_GRID_CHARGE = 3
ACT_SELF_CONSUME = 4
ACT_PAID_IMPORT = 5


@dataclass
class DPConfig:
    """Configuration parameters needed by the DP engine."""

    min_sell_price: float
    min_discharge_price: float
    battery_max_discharge_power: float
    battery_max_charge_power: float
    battery_min_soc: int
    battery_capacity: float
    min_energy_to_discharge: float = 0.0
    disable_discharge: bool = False


def hours_from_now(price_entry: dict) -> float:
    """Calculate hours from now for a price entry."""
    now = dt_util.now()
    entry_date = price_entry.get("date", now.strftime("%Y-%m-%d"))
    entry_hour = price_entry.get("hour", 0)

    try:
        entry_time = datetime.strptime(f"{entry_date} {entry_hour}:00", "%Y-%m-%d %H:%M")
        entry_time = entry_time.replace(tzinfo=now.tzinfo)
        return (entry_time - now).total_seconds() / 3600
    except ValueError:
        return 0


def get_cvcc_charge_multiplier(soc: float) -> float:
    """Calculate the charge power multiplier based on CVCC battery characteristics."""
    if soc < 93.0:
        return 1.0
    if soc < 95.0:
        return 0.80
    if soc < 97.0:
        return 0.50
    if soc < 99.0:
        return 0.25
    return 0.10


def run_unified_dp(
    slots: list[dict[str, Any]],
    current_usable: float,
    usable_capacity: float,
    cycle_cost: float,
    terminal_value_per_kwh: float,
    min_end_usable: float,
    config: DPConfig,
    remaining_hour_fraction: float = 1.0,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Unified DP with six inverter actions.

    DIS: discharge battery to grid (Sell All mode)
    SOL: battery idle, PV surplus to grid (Sell Surplus mode)
    PV_CHARGE: PV surplus charges battery, overflow to grid (Default mode)
    GRID_CHARGE: charge battery from grid (Buy mode)
    SELF_CONSUME: battery covers consumption deficit (no grid export)
    PAID_IMPORT: home from grid + PV curtailed (only when buy_price < 0, paid to consume)

    Returns (charge_hours, discharge_hours, pv_charge_hours, self_consume_hours,
    paid_import_hours, stats).
    """
    empty_stats = {
        "slot_count": len(slots),
        "initial_usable": current_usable,
        "terminal_value_per_kwh": terminal_value_per_kwh,
        "min_end_usable": min_end_usable,
        "planned_export_kwh": 0.0,
        "planned_grid_charge_kwh": 0.0,
        "planned_paid_import_kwh": 0.0,
        "pv_charge_hours": 0,
        "paid_import_hours": 0,
    }
    if not slots or usable_capacity <= 0 or config.battery_capacity <= 0.0:
        return [], [], [], [], [], empty_stats

    # Clone slots to avoid mutating input objects (and scale the first slot)
    scaled_slots = [dict(s) for s in slots]
    if scaled_slots and remaining_hour_fraction < 1.0:
        first_slot = scaled_slots[0]
        if "pv_kwh" in first_slot:
            first_slot["pv_kwh"] = first_slot["pv_kwh"] * remaining_hour_fraction
        if "consumption_kwh" in first_slot:
            first_slot["consumption_kwh"] = first_slot["consumption_kwh"] * remaining_hour_fraction
        if "ev_kwh" in first_slot:
            first_slot["ev_kwh"] = first_slot["ev_kwh"] * remaining_hour_fraction

    energy_step = 0.1
    max_energy_idx = max(0, int(round(usable_capacity / energy_step)))
    initial_idx = min(max_energy_idx, max(0, int(round(current_usable / energy_step))))
    neg_inf = float("-inf")

    # Pre-calculate CVCC charge multipliers for all states to optimize loop performance
    cvcc_multipliers = []
    for s_idx in range(max_energy_idx + 1):
        usable_energy = s_idx * energy_step
        soc_val = config.battery_min_soc + (usable_energy / config.battery_capacity * 100.0)
        clamped_soc = min(100.0, max(0.0, soc_val))
        cvcc_multipliers.append(get_cvcc_charge_multiplier(clamped_soc))

    n_slots = len(slots)
    dp: list[list[float]] = [
        [neg_inf] * (max_energy_idx + 1) for _ in range(n_slots + 1)
    ]
    prev_state: list[list[int]] = [
        [-1] * (max_energy_idx + 1) for _ in range(n_slots + 1)
    ]
    prev_type: list[list[int]] = [
        [ACT_SOL] * (max_energy_idx + 1) for _ in range(n_slots + 1)
    ]
    prev_amount: list[list[float]] = [
        [0.0] * (max_energy_idx + 1) for _ in range(n_slots + 1)
    ]

    dp[0][initial_idx] = 0.0

    for slot_idx, slot in enumerate(scaled_slots, start=1):
        sell_price = slot.get("sell_price", 0.0)
        buy_price = slot.get("buy_price", 0.0)
        pv_kwh = slot.get("pv_kwh", 0.0)
        consumption_kwh = slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0)
        pv_surplus = max(0.0, pv_kwh - consumption_kwh)
        pv_deficit = max(0.0, consumption_kwh - pv_kwh)
        override = slot.get("override")
        # Parse override: may be "action" or "action:target_soc"
        override_action = None
        override_target_soc = None
        target_nsi = None
        if override:
            _parts = override.split(":", 1)
            override_action = _parts[0]
            if len(_parts) == 2:
                try:
                    override_target_soc = float(_parts[1])
                except (ValueError, TypeError):
                    override_target_soc = None
            else:
                if override_action == "grid_charge":
                    override_target_soc = 100.0
                elif override_action == "discharge":
                    override_target_soc = float(config.battery_min_soc)

            if override_target_soc is not None and config.battery_capacity > 0.0 and energy_step > 0.0:
                target_usable = config.battery_capacity * (override_target_soc - config.battery_min_soc) / 100.0
                target_nsi = max(0, min(max_energy_idx, int(round(target_usable / energy_step))))

        for state_idx, current_value in enumerate(dp[slot_idx - 1]):
            if current_value == neg_inf:
                continue

            usable_energy = state_idx * energy_step
            state_updated = False

            def _update(nsi: int, rwd: float, act: int, amt: float) -> None:
                nonlocal state_updated
                # Defense-in-depth safety checks
                if target_nsi is not None and not (act == ACT_SOL and nsi == state_idx):
                    if target_nsi > state_idx:
                        if nsi > target_nsi or nsi <= state_idx:
                            return
                    elif target_nsi < state_idx:
                        if nsi < target_nsi or nsi >= state_idx:
                            return
                    else:
                        if nsi != state_idx:
                            return
                val = current_value + rwd
                if val > dp[slot_idx][nsi]:
                    dp[slot_idx][nsi] = val
                    prev_state[slot_idx][nsi] = state_idx
                    prev_type[slot_idx][nsi] = act
                    prev_amount[slot_idx][nsi] = amt
                state_updated = True

            # === SOL: battery idle, PV surplus -> grid ===
            if (not override_action or override_action in ("idle", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat", "self_consume")) and (target_nsi is None or target_nsi == state_idx):
                is_sol_allowed = True
                if override_action:
                    if override_action in ("sale_pv", "sale_pv_bat", "self_consume"):
                        if pv_surplus > 0 and avail_cap >= energy_step:
                            is_sol_allowed = False
                        elif pv_deficit >= energy_step and usable_energy >= energy_step:
                            is_sol_allowed = False
                    elif override_action == "stop_sale":
                        if (pv_surplus > 0 and avail_cap >= energy_step) or (pv_deficit >= energy_step and usable_energy >= energy_step):
                            is_sol_allowed = False
                if is_sol_allowed:
                    _update(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

            # === DIS: discharge battery to grid ===
            if (override_action == "discharge" or ((not config.disable_discharge and not override_action) and sell_price >= config.min_discharge_price and sell_price > 0)) and (target_nsi is None or target_nsi < state_idx):
                max_discharge_power = config.battery_max_discharge_power
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_discharge_power *= remaining_hour_fraction
                max_exp = min(max_discharge_power, usable_energy)
                
                if target_nsi is not None:
                    max_possible_dis_steps = int(max_exp / energy_step)
                    desired_dis_steps = min(state_idx - target_nsi, max_possible_dis_steps)
                    if desired_dis_steps >= 1:
                        exp = desired_dis_steps * energy_step
                        nsi = max(0, min(max_energy_idx, state_idx - desired_dis_steps))
                        to_grid = max(0.0, exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - exp - pv_kwh)
                        _update(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)
                else:
                    min_ei = 1
                    max_ei = int(round(max_exp / energy_step))
                    for ei in range(min_ei, max_ei + 1):
                        exp = ei * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - exp) / energy_step))))
                        to_grid = max(0.0, exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - exp - pv_kwh)
                        _update(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)

            # === PV_CHARGE: PV surplus -> battery, overflow -> grid ===
            avail_cap = usable_capacity - usable_energy
            if (not override_action or override_action in ("grid_charge", "pv_charge", "sale_pv", "sale_pv_bat", "stop_sale", "self_consume")) and pv_surplus > 0 and avail_cap >= energy_step and (target_nsi is None or target_nsi > state_idx):
                max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_charge_power *= remaining_hour_fraction
                max_pvc = min(pv_surplus, avail_cap, max_charge_power)
                
                if target_nsi is not None:
                    max_possible_chg_steps = int(max_pvc / energy_step)
                    desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps)
                    if desired_chg_steps >= 1:
                        chg = desired_chg_steps * energy_step
                        nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
                        reward = sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update(nsi, reward, ACT_PV_CHARGE, chg)
                else:
                    for ci in range(1, int(max_pvc / energy_step) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        reward = sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update(nsi, reward, ACT_PV_CHARGE, chg)

            # === GRID_CHARGE: charge battery from grid ===
            if (not override_action or override_action == "grid_charge") and avail_cap >= energy_step and (target_nsi is None or target_nsi > state_idx):
                max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_charge_power *= remaining_hour_fraction
                max_gc = min(max_charge_power, avail_cap)
                
                if target_nsi is not None:
                    max_possible_chg_steps = int(max_gc / energy_step)
                    desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps)
                    if desired_chg_steps >= 1:
                        chg = desired_chg_steps * energy_step
                        nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
                        _update(nsi, sell_price * pv_surplus - buy_price * (chg + pv_deficit) - cycle_cost * chg, ACT_GRID_CHARGE, chg)
                else:
                    for ci in range(1, int(max_gc / energy_step) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        _update(nsi, sell_price * pv_surplus - buy_price * (chg + pv_deficit) - cycle_cost * chg, ACT_GRID_CHARGE, chg)

            # === SELF_CONSUME: battery covers consumption deficit ===
            if (not override_action or override_action in ("self_consume", "stop_sale", "sale_pv", "sale_pv_bat", "discharge")) and pv_deficit >= energy_step and usable_energy >= energy_step and (target_nsi is None or target_nsi < state_idx):
                max_sc = min(usable_energy, pv_deficit)
                
                if target_nsi is not None:
                    max_possible_sc_steps = int(max_sc / energy_step)
                    desired_sc_steps = min(state_idx - target_nsi, max_possible_sc_steps)
                    if desired_sc_steps >= 1:
                        sc = desired_sc_steps * energy_step
                        nsi = max(0, min(max_energy_idx, state_idx - desired_sc_steps))
                        remaining_deficit = max(0.0, pv_deficit - sc)
                        _update(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)
                else:
                    for sci in range(1, int(round(max_sc / energy_step)) + 1):
                        sc = sci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - sc) / energy_step))))
                        remaining_deficit = max(0.0, pv_deficit - sc)
                        _update(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)

            # === PAID_IMPORT: home from grid, PV curtailed, battery untouched ===
            if not override_action and buy_price < 0 and consumption_kwh >= energy_step:
                _update(state_idx, -buy_price * consumption_kwh, ACT_PAID_IMPORT, 0.0)

            # Fallback to SOL if override was blocked by physical limits (e.g. Empty battery discharging)
            if not state_updated:
                _update(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)


    # Terminal value with reserve enforcement
    min_end_idx = max(0, int(round(min_end_usable / energy_step)))

    best_final_idx = 0
    best_total_value = neg_inf
    for state_idx, value in enumerate(dp[n_slots]):
        if value == neg_inf:
            continue
        if state_idx < min_end_idx:
            continue
        usable_energy = state_idx * energy_step
        total_value = value + usable_energy * terminal_value_per_kwh
        if total_value > best_total_value:
            best_total_value = total_value
            best_final_idx = state_idx

    if best_total_value == neg_inf:
        for state_idx, value in enumerate(dp[n_slots]):
            if value == neg_inf:
                continue
            usable_energy = state_idx * energy_step
            total_value = value + usable_energy * terminal_value_per_kwh
            if total_value > best_total_value:
                best_total_value = total_value
                best_final_idx = state_idx
        if best_total_value != neg_inf:
            _LOGGER.warning(
                "Could not satisfy energy reserve %.1f kWh, relaxing constraint",
                min_end_usable,
            )

    if best_total_value == neg_inf:
        return [], [], [], [], [], empty_stats

    # Backtrack
    types_by_slot = [ACT_SOL] * n_slots
    amounts_by_slot = [0.0] * n_slots
    state_idx = best_final_idx
    for slot_idx in range(n_slots, 0, -1):
        types_by_slot[slot_idx - 1] = prev_type[slot_idx][state_idx]
        amounts_by_slot[slot_idx - 1] = prev_amount[slot_idx][state_idx]
        state_idx = prev_state[slot_idx][state_idx]
        if state_idx < 0:
            break

    # Build result lists
    charge_hours: list[dict[str, Any]] = []
    discharge_hours: list[dict[str, Any]] = []
    pv_charge_hours: list[dict[str, Any]] = []
    self_consume_hours: list[dict[str, Any]] = []
    paid_import_hours: list[dict[str, Any]] = []
    usable_energy = current_usable
    total_export = 0.0
    total_battery_discharge = 0.0
    total_grid_charge = 0.0
    total_paid_import = 0.0
    expected_trajectory: list[float] = []

    for slot, act, amount in zip(scaled_slots, types_by_slot, amounts_by_slot, strict=False):
        start_usable = usable_energy
        soc_val = max(0.0, min(100.0, config.battery_min_soc + (start_usable / config.battery_capacity * 100.0)))
        expected_trajectory.append(round(soc_val, 2))

        # Apply post-processing filter for small grid discharges (exporters)
        if act == ACT_DIS and amount > 0:
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0)
            home_deficit = max(0.0, total_consumption - slot["pv_kwh"])
            battery_to_home = min(amount, home_deficit)
            battery_to_grid = max(0.0, amount - battery_to_home)

            if battery_to_grid > 0.0 and battery_to_grid < config.min_energy_to_discharge:
                # If grid export portion is below limit, switch to self-consumption or idle
                amount = round(battery_to_home, 2)
                if amount > 0.0:
                    act = ACT_SELF_CONSUME
                else:
                    act = ACT_SOL
                    amount = 0.0

        if act == ACT_DIS and amount > 0:
            end_usable = usable_energy - amount
            total_battery_discharge += amount
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0)
            home_deficit = max(0.0, total_consumption - slot["pv_kwh"])
            battery_to_home = min(amount, home_deficit)
            battery_to_grid = max(0.0, amount - battery_to_home)
            grid_import = max(0.0, home_deficit - amount)
            total_export += battery_to_grid
            soc_limit = max(
                config.battery_min_soc,
                config.battery_min_soc + (max(0.0, end_usable) / config.battery_capacity * 100),
            )
            discharge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "value": slot["sell_price"],
                "buy_price": slot["buy_price"],
                "pv_kwh": slot["pv_kwh"],
                "consumption_kwh": slot["consumption_kwh"],
                "ev_kwh": slot.get("ev_kwh", 0.0),
                "profit": slot["sell_price"] - cycle_cost,
                "hours_from_now": hours_from_now(slot),
                "planned_energy_kwh": round(amount, 2),
                "planned_battery_out_kwh": round(amount, 2),
                "planned_export_kwh": round(battery_to_grid, 2),
                "planned_home_supply_kwh": round(battery_to_home, 2),
                "planned_grid_import_kwh": round(grid_import, 2),
                "soc_limit": round(soc_limit, 2),
                "expected_start_usable_kwh": round(start_usable, 2),
                "expected_end_usable_kwh": round(end_usable, 2),
            })
            usable_energy = end_usable

        elif act == ACT_PV_CHARGE and amount > 0:
            end_usable = min(usable_capacity, usable_energy + amount)
            pv_charge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "charge_kwh": round(amount, 2),
                "pv_kwh": slot["pv_kwh"],
                "sell_price": slot["sell_price"],
                "expected_start_usable_kwh": round(start_usable, 2),
                "expected_end_usable_kwh": round(end_usable, 2),
            })
            usable_energy = end_usable

        elif act == ACT_GRID_CHARGE and amount > 0:
            end_usable = min(usable_capacity, usable_energy + amount)
            total_grid_charge += amount
            charge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "value": slot["buy_price"],
                "effective_price": slot["buy_price"] + cycle_cost,
                "hours_from_now": hours_from_now(slot),
                "planned_energy_kwh": round(amount, 2),
                "expected_start_usable_kwh": round(start_usable, 2),
                "expected_end_usable_kwh": round(end_usable, 2),
            })
            usable_energy = end_usable

        elif act == ACT_SELF_CONSUME and amount > 0:
            end_usable = max(0.0, usable_energy - amount)
            self_consume_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "sell_price": slot["sell_price"],
                "buy_price": slot["buy_price"],
                "pv_kwh": slot["pv_kwh"],
                "consumption_kwh": slot["consumption_kwh"],
                "planned_energy_kwh": round(amount, 2),
                "expected_start_usable_kwh": round(start_usable, 2),
                "expected_end_usable_kwh": round(end_usable, 2),
            })
            usable_energy = end_usable

        elif act == ACT_PAID_IMPORT:
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0)
            total_paid_import += total_consumption
            paid_import_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "buy_price": slot["buy_price"],
                "consumption_kwh": slot["consumption_kwh"],
                "ev_kwh": slot.get("ev_kwh", 0.0),
                "pv_kwh": slot["pv_kwh"],
                "planned_grid_import_kwh": round(total_consumption, 2),
                "expected_revenue": round(-slot["buy_price"] * total_consumption, 4),
                "expected_start_usable_kwh": round(start_usable, 2),
                "expected_end_usable_kwh": round(start_usable, 2),
            })

    return charge_hours, discharge_hours, pv_charge_hours, self_consume_hours, paid_import_hours, {
        "slot_count": n_slots,
        "initial_usable": round(current_usable, 2),
        "terminal_value_per_kwh": round(terminal_value_per_kwh, 4),
        "min_end_usable": round(min_end_usable, 2),
        "planned_battery_discharge_kwh": round(total_battery_discharge, 2),
        "planned_export_kwh": round(total_export, 2),
        "planned_grid_charge_kwh": round(total_grid_charge, 2),
        "planned_paid_import_kwh": round(total_paid_import, 2),
        "pv_charge_hours": len(pv_charge_hours),
        "paid_import_hours": len(paid_import_hours),
        "best_value": round(best_total_value, 2),
        "end_usable_kwh": round(usable_energy, 2),
        "expected_trajectory": expected_trajectory,
    }
