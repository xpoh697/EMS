"""Dynamic Programming engine for EMS."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util
from .const import INVERTER_MODES
from .utils import map_dp_to_physical
from .utils import map_override_to_physical

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
    battery_min_soc_evening: float | None = None
    battery_min_soc_morning: float | None = None
    min_arbitrage_profit: float = 0.0
    eta_charge: float = 1.0
    eta_discharge: float = 1.0
    last_sell_hour_by_date: dict = field(default_factory=dict)

    def get_min_soc(self, hour: int, date: str | None = None) -> float:
        """Get the hourly minimum battery SOC based on the 12:00 reference point.

        1. Before 12:00 (0 <= hour < 12): always morning min SOC.
        2. From 12:00 to 23:59:
           - If a sale (discharge) is planned on that date between 12 and 23:
             - From 12:00 up to the last sale hour: evening min SOC (to hold charge for sale).
             - After the last sale hour: back to morning min SOC.
           - If no sale is planned after 12:00: morning min SOC all day.
        """
        min_ev = self.battery_min_soc_evening if self.battery_min_soc_evening is not None else float(self.battery_min_soc)
        min_mo = self.battery_min_soc_morning if self.battery_min_soc_morning is not None else float(self.battery_min_soc)
        if hour < 12:
            return min_mo

        # Hour >= 12: check if there is a planned sale on this date
        if date and date in self.last_sell_hour_by_date:
            last_sell = self.last_sell_hour_by_date[date]
            if 12 <= hour <= last_sell:
                return min_ev
            return min_mo

        # If date is not in last_sell_hour_by_date, but date is not passed, fallback
        if date is None:
            if 0 <= hour < 10:
                return min_mo
            return min_ev

        # Date is known and has no sales in [12..23] -> morning min SOC
        return min_mo


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
    if soc < 84.0:
        return 1.0
    if soc < 87.0:
        return 0.85
    if soc < 90.0:
        return 0.65
    if soc < 93.0:
        return 0.45
    if soc < 96.0:
        return 0.28
    if soc < 98.0:
        return 0.15
    if soc < 99.0:
        return 0.08
    return 0.05


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

    _LOGGER.info("EMS DP: DC-coupled PV charging v2 active")
    # Clone slots to avoid mutating input objects (and scale the first slot)
    scaled_slots = [dict(s) for s in slots]
    today_date = slots[0].get("date") if slots else None
    if today_date:
        for s in scaled_slots:
            if s.get("date") != today_date and s.get("hour", 0) >= 10:
                # 15% uncertainty discount for future day slots (from 08:00 onwards)
                # to prevent withholding energy today for marginal speculative savings tomorrow,
                # while keeping tonight's immediate night horizon (00:00..07:00) at real tariffs.
                # Only discount speculative future sell revenue to avoid withholding energy today.
                # Never discount buy_price so that true night vs day tariffs are evaluated accurately.
                if "sell_price" in s:
                    s["sell_price"] = s["sell_price"] * 0.85

    if scaled_slots and remaining_hour_fraction < 1.0:
        first_slot = scaled_slots[0]
        if "pv_kwh" in first_slot:
            first_slot["pv_kwh"] = first_slot["pv_kwh"] * remaining_hour_fraction
        if "consumption_kwh" in first_slot:
            first_slot["consumption_kwh"] = first_slot["consumption_kwh"] * remaining_hour_fraction
        if "ev_kwh" in first_slot:
            first_slot["ev_kwh"] = first_slot["ev_kwh"] * remaining_hour_fraction
        if "planned_boiler_kwh" in first_slot:
            first_slot["planned_boiler_kwh"] = first_slot["planned_boiler_kwh"] * remaining_hour_fraction

    energy_step = 0.1
    max_energy_idx = max(0, int(round(usable_capacity / energy_step)))
    initial_idx = min(max_energy_idx, max(0, int(round(current_usable / energy_step))))
    neg_inf = float("-inf")

    # Pre-calculate reference night buy price for pre-solar export arbitrage check
    night_buys = [
        s.get("buy_price", 0.0) 
        for s in scaled_slots 
        if s.get("hour", 0) in (22, 23, 0, 1, 2, 3, 4, 5) and s.get("pv_kwh", 0.0) <= 0.5
    ]
    ref_night_buy = max(night_buys) if night_buys else 1.0
    min_day_buy = min((s.get("buy_price", 99.0) for s in scaled_slots), default=ref_night_buy)
    min_arbitrage_margin = config.min_arbitrage_profit if config.min_arbitrage_profit > 0.0 else 0.30

    # Pre-calculate CVCC charge multipliers for all states to optimize loop performance
    current_hour = scaled_slots[0]["hour"] if scaled_slots else 0
    current_min_soc = config.get_min_soc(current_hour)
    cvcc_multipliers = []
    for s_idx in range(max_energy_idx + 1):
        usable_energy = s_idx * energy_step
        soc_val = config.battery_min_soc + (usable_energy / config.battery_capacity * 100.0)
        clamped_soc = min(100.0, max(0.0, soc_val))
        cvcc_multipliers.append(get_cvcc_charge_multiplier(clamped_soc))

    n_slots = len(slots)

    # 1. Find first override slot (1-based index)
    first_override_slot_idx = n_slots + 1
    for idx, slot in enumerate(scaled_slots, start=1):
        if slot.get("override"):
            first_override_slot_idx = idx
            break

    # 2. First Pass: normal optimization (no overrides)
    dp_normal = None
    prev_state_normal = None
    prev_type_normal = None
    prev_amount_normal = None
    optimal_states_normal = None

    if 1 < first_override_slot_idx <= n_slots:
        dp_n = [[neg_inf] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_s_n = [[-1] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_t_n = [[ACT_SOL] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_a_n = [[0.0] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        dp_n[0][initial_idx] = 0.0

        for slot_idx, slot in enumerate(scaled_slots, start=1):
            sell_price = slot.get("sell_price", 0.0)
            buy_price = slot.get("buy_price", 0.0)
            pv_kwh = slot.get("pv_kwh", 0.0)
            consumption_kwh = slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)
            pv_surplus = max(0.0, pv_kwh - consumption_kwh)
            pv_deficit = max(0.0, consumption_kwh - pv_kwh)
            # When sell_price is below min_sell_price the mode mapper will choose STOP_SALE
            # (no PV export). Use effective_sell_price=0 so the DP doesn't credit
            # phantom export revenue and correctly prefers PV charging instead.
            effective_sell_price = sell_price if sell_price >= config.min_sell_price else 0.0
            slot_hour = slot.get("hour", 0)
            is_pre_solar = slot_hour < 6 and slot.get("pv_kwh", 0.0) <= 0.5
            is_pre_solar_low_price = (slot_hour in (5, 6, 7, 8) and slot.get("pv_kwh", 0.0) <= 0.5
                                       and sell_price < config.min_discharge_price)
            eff_sell_net = sell_price * config.eta_discharge - cycle_cost
            can_export = (not is_pre_solar) or ((eff_sell_net - ref_night_buy) >= min_arbitrage_margin)

            slot_min_soc = config.get_min_soc(slot.get("hour", 0), slot.get("date"))
            min_usable_allowed = config.battery_capacity * max(0.0, slot_min_soc - config.battery_min_soc) / 100.0
            min_slot_idx = int(round(min_usable_allowed / energy_step))

            pre_solar_ahead = []
            for _f in scaled_slots[slot_idx:]:
                if _f.get("pv_kwh", 0.0) > 0.5 or (_f.get("hour", 0) >= 10 and (_f.get("date") != slot.get("date") or slot.get("hour", 0) < 10)):
                    break
                pre_solar_ahead.append(_f)
            home_needed = (sum(max(0.0, _f.get("consumption_kwh", 0.0) + _f.get("ev_kwh", 0.0) + _f.get("planned_boiler_kwh", 0.0) - _f.get("pv_kwh", 0.0)) for _f in pre_solar_ahead) / (config.eta_discharge if config.eta_discharge > 0.0 else 1.0)) if pre_solar_ahead else 0.0
            higher_sell_cap = sum(
                min(config.battery_max_discharge_power, config.battery_capacity)
                for _f in pre_solar_ahead
                if _f.get("sell_price", 0.0) > (sell_price + 0.02)
            ) if pre_solar_ahead else 0.0
            night_recharge_buys = [_f.get("buy_price", 99.0) for _f in pre_solar_ahead if _f.get("hour", 0) in (22, 23, 0, 1, 2, 3, 4, 5)]
            max_night_buy = max(night_recharge_buys) if night_recharge_buys else (max((_f.get("buy_price", 0.0) for _f in pre_solar_ahead), default=0.0))
            min_night_buy = min(night_recharge_buys) if night_recharge_buys else max_night_buy
            eff_sell_net = sell_price * config.eta_discharge - cycle_cost
            recharge_replacement_cost = (min_night_buy / config.eta_charge + cycle_cost) if config.eta_charge > 0.0 else min_night_buy
            discharge_arbitrage_ok = (eff_sell_net >= recharge_replacement_cost) or (eff_sell_net >= max_night_buy)

            for state_idx, current_value in enumerate(dp_n[slot_idx - 1]):
                if current_value == neg_inf:
                    continue

                usable_energy = state_idx * energy_step
                state_updated = False

                def _update_n(nsi: int, rwd: float, act: int, amt: float) -> None:
                    nonlocal state_updated
                    val = current_value + rwd
                    if val > dp_n[slot_idx][nsi]:
                        dp_n[slot_idx][nsi] = val
                        prev_s_n[slot_idx][nsi] = state_idx
                        prev_t_n[slot_idx][nsi] = act
                        prev_a_n[slot_idx][nsi] = amt
                    state_updated = True

                # === SOL ===
                _update_n(state_idx, effective_sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

                # === DIS ===
                if not config.disable_discharge and sell_price >= config.min_discharge_price and sell_price > 0:
                    max_discharge_power = config.battery_max_discharge_power
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_discharge_power *= remaining_hour_fraction
                    max_exp = min(max_discharge_power, usable_energy)
                    min_ei = 1
                    max_ei = int(round(max_exp / energy_step))
                    for ei in range(min_ei, max_ei + 1):
                        exp = ei * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - exp) / energy_step))))
                        if nsi < min_slot_idx:
                            continue
                        ac_exp = exp * config.eta_discharge
                        to_grid = max(0.0, ac_exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - ac_exp - pv_kwh)
                        if to_grid > 0.0:
                            if exp < config.min_energy_to_discharge or not can_export:
                                continue
                            if is_pre_solar_low_price:
                                continue
                            req_reserve = (higher_sell_cap + home_needed) if (not discharge_arbitrage_ok and slot_hour < 12) else higher_sell_cap
                            if req_reserve > 0.0 and (usable_energy - exp) < req_reserve:
                                continue
                            if (usable_energy - exp) < home_needed and not discharge_arbitrage_ok:
                                continue
                        _update_n(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)

                # === PV_CHARGE ===
                avail_cap = usable_capacity - usable_energy
                if pv_surplus > 0 and avail_cap >= energy_step:
                    max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    max_pvc = min(pv_surplus, avail_cap, max_charge_power)
                    steps = int((max_pvc + 1e-6) / energy_step)
                    if steps == 0 and slot_idx == 1 and remaining_hour_fraction < 1.0 and pv_surplus > 0.001:
                        steps = 1
                    for ci in range(1, steps + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        req_chg = chg / config.eta_charge if config.eta_charge > 0.0 else chg
                        reward = effective_sell_price * max(0.0, pv_surplus - req_chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update_n(nsi, reward, ACT_PV_CHARGE, chg)

                # === GRID_CHARGE ===
                min_gc_power = max(0.5, float(config.min_energy_to_discharge or 0.5))
                max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                if avail_cap >= energy_step and (max_charge_power >= min_gc_power or slot_idx == 1):
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    max_gc = min(max_charge_power, avail_cap)

                    if config.min_arbitrage_profit > 0.0:
                        curr_slot = scaled_slots[slot_idx - 1]
                        is_pre_solar_night = curr_slot.get("pv_kwh", 0.0) <= 0.5 and curr_slot.get("hour", 0) in (22, 23, 0, 1, 2, 3, 4, 5, 6)
                        eta_rt = (config.eta_charge * config.eta_discharge) if (config.eta_charge > 0.0 and config.eta_discharge > 0.0) else 1.0
                        eff_stored_cost = (buy_price / eta_rt) + cycle_cost

                        if is_pre_solar_night:
                            pre_solar_slots = []
                            for _ps in scaled_slots[slot_idx - 1:]:
                                if _ps.get("pv_kwh", 0.0) > 0.5 or _ps.get("hour", 0) in range(10, 22):
                                    break
                                pre_solar_slots.append(_ps)
                            sum_pv_ahead = sum(s.get("pv_kwh", 0.0) for s in scaled_slots[slot_idx - 1:])
                            max_pre_solar_sell = max((s.get("sell_price", 0.0) for s in pre_solar_slots), default=0.0)
                            cheaper_future_buy = any(s.get("buy_price", 0.0) < (buy_price - 0.01) for s in scaled_slots[slot_idx:])
                            max_all_sell = max((s.get("sell_price", 0.0) for s in scaled_slots[slot_idx - 1:]), default=0.0)
                            all_arb_margin = (max_all_sell * config.eta_discharge) - (buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price) - cycle_cost
                            eval_to_pre_solar = (
                                (max_pre_solar_sell >= config.min_discharge_price and all_arb_margin < config.min_arbitrage_profit)
                                or cheaper_future_buy
                                or sum_pv_ahead >= max(0.0, usable_capacity - usable_energy)
                            )
                            eval_slots = pre_solar_slots if eval_to_pre_solar else scaled_slots[slot_idx - 1:]

                            max_future_sell = max((s.get("sell_price", 0.0) for s in eval_slots), default=0.0)
                            effective_future_sell = max_future_sell * config.eta_discharge
                            effective_buy_cost = buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price
                            arbitrage_margin = effective_future_sell - effective_buy_cost - cycle_cost
                            if arbitrage_margin < config.min_arbitrage_profit:
                                home_deficit_until_solar = 0.0
                                for f_slot in pre_solar_slots:
                                    if f_slot.get("buy_price", 0.0) > eff_stored_cost:
                                        f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                        net_def = max(0.0, f_cons - f_slot.get("pv_kwh", 0.0))
                                        home_deficit_until_solar += (net_def / config.eta_discharge if config.eta_discharge > 0.0 else net_def)
                                pre_solar_needed = max(0.0, max(home_deficit_until_solar, 1.0 if home_deficit_until_solar > 0.1 else 0.0) - usable_energy)
                                if not cheaper_future_buy:
                                    sum_day_home_def = 0.0
                                    sum_pv_ahead = 0.0
                                    for f_slot in scaled_slots[slot_idx - 1:]:
                                        if f_slot.get("date") != curr_slot.get("date"):
                                            break
                                        f_pv = f_slot.get("pv_kwh", 0.0)
                                        f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                        if f_pv > f_cons:
                                            sum_pv_ahead += (f_pv - f_cons)
                                        else:
                                            sum_day_home_def += (f_cons - f_pv)
                                    needed_day_deficit = max(0.0, sum_day_home_def - sum_pv_ahead - usable_energy)
                                    space_with_solar_reserve = max(0.0, avail_cap - sum_pv_ahead)
                                    allowed_capacity = max(0.0, min(space_with_solar_reserve, max(pre_solar_needed, needed_day_deficit)))
                                else:
                                    allowed_capacity = max(0.0, pre_solar_needed)
                                max_gc = min(max_gc, allowed_capacity)
                        else:
                            future_slots = scaled_slots[slot_idx - 1:]
                            max_future_sell = max((s.get("sell_price", 0.0) for s in future_slots), default=0.0)
                            effective_future_sell = max_future_sell * config.eta_discharge
                            effective_buy_cost = buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price
                            arbitrage_margin = effective_future_sell - effective_buy_cost - cycle_cost
                            orig_slot = slots[slot_idx - 1]
                            orig_buy = orig_slot.get("buy_price", 0.0)
                            orig_max_future_sell = max((s.get("sell_price", 0.0) for s in slots[slot_idx - 1:]), default=0.0)
                            gross_arbitrage_margin = orig_max_future_sell - orig_buy
                            is_arb_profitable = (arbitrage_margin >= config.min_arbitrage_profit) or (gross_arbitrage_margin >= config.min_arbitrage_profit and arbitrage_margin > 0.0)
                            cheaper_future_buy = any(s.get("buy_price", 0.0) < (buy_price - 0.005) for s in future_slots[1:])
                            if not is_arb_profitable:
                                sum_expensive_deficit = 0.0
                                sum_pv_between = 0.0
                                cheaper_future_capacity = 0.0
                                for f_slot in future_slots[1:]:
                                    f_buy = f_slot.get("buy_price", 0.0)
                                    f_pv = f_slot.get("pv_kwh", 0.0)
                                    f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                    if f_buy < (buy_price - 0.005):
                                        f_net_pv = max(0.0, f_pv - f_cons)
                                        cheaper_future_capacity += max(0.0, config.battery_max_charge_power - f_net_pv)
                                    if f_pv > f_cons:
                                        sum_pv_between += (f_pv - f_cons)
                                    elif f_buy > eff_stored_cost:
                                        net_def = f_cons - f_pv
                                        sum_expensive_deficit += (net_def / config.eta_discharge if config.eta_discharge > 0.0 else net_def)
                                reserve_to_charge = float(min_end_usable or 0.0) if buy_price <= (ref_night_buy + 0.15) else 0.0
                                total_deficit = max(0.0, sum_expensive_deficit + reserve_to_charge - sum_pv_between - usable_energy)
                                net_needed = max(0.0, total_deficit - cheaper_future_capacity)
                                max_future_buy = max((s.get("buy_price", 0.0) for s in future_slots[1:]), default=0.0)
                                eff_saving_vs_future_buy = max_future_buy * config.eta_discharge - eff_stored_cost
                                if eff_saving_vs_future_buy >= 0.15 or buy_price <= 0.25:
                                    spare_capacity_after_solar = max(0.0, avail_cap - sum_pv_between)
                                    home_saving_cap = max(0.0, min(spare_capacity_after_solar, sum_expensive_deficit - usable_energy))
                                    net_needed = max(net_needed, home_saving_cap)
                                max_gc = min(max_gc, net_needed)
                            else:
                                # Arbitrage is profitable, but must not displace cheap solar!
                                # If future solar can be sold at a higher price than current charging cost,
                                # charging is allowed up to that profitable sale volume.
                                pv_cheap_to_reserve = 0.0
                                effective_charge_cost = effective_buy_cost + cycle_cost
                                for f_slot in future_slots[1:]:
                                    f_sell = f_slot.get("sell_price", 0.0)
                                    f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0) + f_slot.get("ev_kwh", 0.0)
                                    f_net_pv = max(0.0, f_slot.get("pv_kwh", 0.0) - f_cons)
                                    if f_sell < effective_charge_cost:
                                        pv_cheap_to_reserve += f_net_pv
                                    if f_sell >= max_future_sell - 1e-4:
                                        break
                                allowed_arbitrage_gc = max(0.0, avail_cap - pv_cheap_to_reserve)
                                max_gc = min(max_gc, allowed_arbitrage_gc)

                    min_ci = 1 if (slot_idx == 1 or avail_cap < config.min_energy_to_discharge) else (max(1, int(round(config.min_energy_to_discharge / energy_step))) if config.min_energy_to_discharge > 0 else 1)
                    for ci in range(min_ci, int(round(max_gc / energy_step)) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        grid_part = max(0.0, chg - pv_surplus)
                        net_chg = grid_part / config.eta_charge if config.eta_charge > 0.0 else grid_part
                        reward = - buy_price * (net_chg + pv_deficit)
                        if slot_idx == 1:
                            reward += 1e-4 * chg
                        _update_n(nsi, reward, ACT_GRID_CHARGE, chg)

                # === SELF_CONSUME ===
                if pv_deficit > 0.001 and usable_energy >= energy_step:
                    needed_dc = pv_deficit / config.eta_discharge if config.eta_discharge > 0.0 else pv_deficit
                    max_sc = min(usable_energy, needed_dc)
                    max_sci = max(1, int(round(max_sc / energy_step)))
                    for sci in range(1, max_sci + 1):
                        sc = sci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - sc) / energy_step))))
                        if nsi < min_slot_idx:
                            continue
                        ac_sc = sc * config.eta_discharge
                        remaining_deficit = max(0.0, pv_deficit - ac_sc)
                        _update_n(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)

                # === PAID_IMPORT ===
                if buy_price < 0 and consumption_kwh >= energy_step:
                    _update_n(state_idx, -buy_price * consumption_kwh, ACT_PAID_IMPORT, 0.0)

                if not state_updated:
                    _update_n(state_idx, effective_sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

        dp_normal = dp_n
        prev_state_normal = prev_s_n
        prev_type_normal = prev_t_n
        prev_amount_normal = prev_a_n

        # Backtrack Pass 1 to find optimal trajectory of normal states
        min_end_idx = max(0, int(round(min_end_usable / energy_step)))
        best_final_idx_normal = 0
        best_total_value_normal = neg_inf
        for state_idx, value in enumerate(dp_normal[n_slots]):
            if value == neg_inf:
                continue
            if state_idx < min_end_idx:
                continue
            usable_energy = state_idx * energy_step
            reserve_energy = min(usable_energy, min_end_usable)
            surplus_energy = max(0.0, usable_energy - min_end_usable)
            total_value = value + reserve_energy * terminal_value_per_kwh + surplus_energy * config.min_sell_price
            if total_value > best_total_value_normal:
                best_total_value_normal = total_value
                best_final_idx_normal = state_idx

        if best_total_value_normal == neg_inf:
            for state_idx, value in enumerate(dp_normal[n_slots]):
                if value == neg_inf:
                    continue
                usable_energy = state_idx * energy_step
                reserve_energy = min(usable_energy, min_end_usable)
                surplus_energy = max(0.0, usable_energy - min_end_usable)
                total_value = value + reserve_energy * terminal_value_per_kwh + surplus_energy * config.min_sell_price
                if total_value > best_total_value_normal:
                    best_total_value_normal = total_value
                    best_final_idx_normal = state_idx

        if best_total_value_normal != neg_inf:
            optimal_states_normal = [0] * (n_slots + 1)
            optimal_states_normal[n_slots] = best_final_idx_normal
            state_idx = best_final_idx_normal
            for slot_idx in range(n_slots, 0, -1):
                state_idx = prev_state_normal[slot_idx][state_idx]
                optimal_states_normal[slot_idx - 1] = state_idx

    # 3. Second Pass: run DP with overrides
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
        if slot_idx < first_override_slot_idx and optimal_states_normal is not None:
            # Enforce exact normal trajectory boundary state
            norm_state = optimal_states_normal[slot_idx]
            prev_norm_state = optimal_states_normal[slot_idx - 1]
            dp[slot_idx][norm_state] = dp_normal[slot_idx][norm_state]
            prev_state[slot_idx][norm_state] = prev_norm_state
            prev_type[slot_idx][norm_state] = prev_type_normal[slot_idx][norm_state]
            prev_amount[slot_idx][norm_state] = prev_amount_normal[slot_idx][norm_state]
            continue

        sell_price = slot.get("sell_price", 0.0)
        buy_price = slot.get("buy_price", 0.0)
        pv_kwh = slot.get("pv_kwh", 0.0)
        consumption_kwh = slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)
        pv_surplus = max(0.0, pv_kwh - consumption_kwh)
        pv_deficit = max(0.0, consumption_kwh - pv_kwh)
        effective_sell_price = sell_price if sell_price >= config.min_sell_price else 0.0
        slot_hour = slot.get("hour", 0)
        is_pre_solar = slot_hour < 6 and slot.get("pv_kwh", 0.0) <= 0.5
        # Pre-solar morning hours: block battery export to grid if sell_price is below min_discharge_price.
        # This prevents "dumping" stored energy cheaply in the morning that could have been sold at
        # higher evening peak prices, forcing battery into self-consume mode instead.
        is_pre_solar_low_price = (slot_hour in (5, 6, 7, 8) and slot.get("pv_kwh", 0.0) <= 0.5
                                   and sell_price < config.min_discharge_price)
        eff_sell_net = sell_price * config.eta_discharge - cycle_cost
        can_export = (not is_pre_solar) or ((eff_sell_net - ref_night_buy) >= min_arbitrage_margin)
        slot_min_soc = config.get_min_soc(slot.get("hour", 0), slot.get("date"))
        min_usable_allowed = config.battery_capacity * max(0.0, slot_min_soc - config.battery_min_soc) / 100.0
        min_slot_idx = int(round(min_usable_allowed / energy_step))
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
                    override_target_soc = float(config.get_min_soc(slot.get("hour", 0), slot.get("date")))

            if override_target_soc is not None and config.battery_capacity > 0.0 and energy_step > 0.0:
                target_usable = config.battery_capacity * (override_target_soc - config.battery_min_soc) / 100.0
                target_nsi = max(0, min(max_energy_idx, int(round(target_usable / energy_step))))

        # Resolve override to physical mode config
        mode_config = None
        if override_action:
            cheap_ahead = False
            if override_action != "self_consume":
                current_idx = slot_idx - 1
                horizon_end = min(current_idx + 7, len(scaled_slots))
                for f_idx in range(current_idx, horizon_end):
                    future_p_buy = scaled_slots[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            physical_mode, _ = map_override_to_physical(
                action=override_action,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=config.min_sell_price,
                min_discharge_price=config.min_discharge_price,
                cheap_ahead=cheap_ahead,
            )
            mode_config = INVERTER_MODES.get(physical_mode)

        pre_solar_ahead = []
        if not override:
            for _f in scaled_slots[slot_idx:]:
                if _f.get("pv_kwh", 0.0) > 0.5 or (_f.get("hour", 0) >= 10 and (_f.get("date") != slot.get("date") or slot.get("hour", 0) < 10)):
                    break
                pre_solar_ahead.append(_f)
        home_needed = (sum(max(0.0, _f.get("consumption_kwh", 0.0) + _f.get("ev_kwh", 0.0) + _f.get("planned_boiler_kwh", 0.0) - _f.get("pv_kwh", 0.0)) for _f in pre_solar_ahead) / (config.eta_discharge if config.eta_discharge > 0.0 else 1.0)) if pre_solar_ahead else 0.0
        higher_sell_cap = sum(
            min(config.battery_max_discharge_power, config.battery_capacity)
            for _f in pre_solar_ahead
            if _f.get("sell_price", 0.0) > (sell_price + 0.02)
        ) if pre_solar_ahead else 0.0
        night_recharge_buys = [_f.get("buy_price", 99.0) for _f in pre_solar_ahead if _f.get("hour", 0) in (22, 23, 0, 1, 2, 3, 4, 5)]
        max_night_buy = max(night_recharge_buys) if night_recharge_buys else (max((_f.get("buy_price", 0.0) for _f in pre_solar_ahead), default=0.0))
        min_night_buy = min(night_recharge_buys) if night_recharge_buys else max_night_buy
        eff_sell_net = sell_price * config.eta_discharge - cycle_cost
        recharge_replacement_cost = (min_night_buy / config.eta_charge + cycle_cost) if config.eta_charge > 0.0 else min_night_buy
        discharge_arbitrage_ok = (eff_sell_net >= recharge_replacement_cost) or (eff_sell_net >= max_night_buy)

        for state_idx, current_value in enumerate(dp[slot_idx - 1]):
            if current_value == neg_inf:
                continue

            usable_energy = state_idx * energy_step
            avail_cap = usable_capacity - usable_energy
            state_updated = False

            # Calculate the exact expected target state for this starting state under override
            expected_nsi = state_idx
            if target_nsi is not None:
                if target_nsi > state_idx:
                    max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    avail_cap = usable_capacity - usable_energy
                    max_gc = min(max_charge_power, max(energy_step, avail_cap))
                    max_possible_chg_steps = int(round(max_gc / energy_step))
                    if max_possible_chg_steps == 0 and slot_idx == 1 and target_nsi > state_idx:
                        max_possible_chg_steps = 1
                    expected_nsi = min(target_nsi, state_idx + max_possible_chg_steps)
                elif target_nsi < state_idx:
                    max_discharge_power = config.battery_max_discharge_power
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_discharge_power *= remaining_hour_fraction
                    max_exp = min(max_discharge_power, usable_energy)
                    max_possible_dis_steps = int(round(max_exp / energy_step))
                    expected_nsi = max(target_nsi, state_idx - max_possible_dis_steps)

            def _update(nsi: int, rwd: float, act: int, amt: float) -> None:
                nonlocal state_updated
                # Defense-in-depth safety checks
                if target_nsi is not None and not (act == ACT_SOL and nsi == state_idx):
                    if nsi != expected_nsi:
                        return
                val = current_value + rwd
                if val > dp[slot_idx][nsi]:
                    dp[slot_idx][nsi] = val
                    prev_state[slot_idx][nsi] = state_idx
                    prev_type[slot_idx][nsi] = act
                    prev_amount[slot_idx][nsi] = amt
                state_updated = True

            # === SOL: battery idle, PV surplus -> grid ===
            if (not override_action or (mode_config and mode_config.name in ("idle", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat"))) and (target_nsi is None or target_nsi == state_idx):
                is_sol_allowed = True
                if mode_config:
                    if mode_config.charge_from_pv and pv_surplus > 0 and avail_cap >= energy_step:
                        is_sol_allowed = False
                    elif mode_config.discharge_to_house and pv_deficit > 0.001 and usable_energy >= energy_step:
                        is_sol_allowed = False
                if is_sol_allowed:
                    _update(state_idx, effective_sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

            # === DIS: discharge battery to grid ===
            if ((not override_action and (not config.disable_discharge and sell_price >= config.min_discharge_price and sell_price > 0)) or (mode_config and mode_config.discharge_to_grid)) and (target_nsi is None or target_nsi < state_idx):
                max_discharge_power = config.battery_max_discharge_power
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_discharge_power *= remaining_hour_fraction
                max_exp = min(max_discharge_power, usable_energy)
                
                if target_nsi is not None:
                    max_possible_dis_steps = int(round(max_exp / energy_step))
                    desired_dis_steps = min(state_idx - target_nsi, max_possible_dis_steps)
                    if desired_dis_steps >= 1:
                        exp = desired_dis_steps * energy_step
                        nsi = max(0, min(max_energy_idx, state_idx - desired_dis_steps))
                        ac_exp = exp * config.eta_discharge
                        to_grid = max(0.0, ac_exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - ac_exp - pv_kwh)
                        blocked = False
                        if not override and to_grid > 0.0:
                            if not can_export:
                                blocked = True
                            elif is_pre_solar_low_price:
                                blocked = True
                            else:
                                req_reserve = (higher_sell_cap + home_needed) if (not discharge_arbitrage_ok and slot_hour < 12) else higher_sell_cap
                                if req_reserve > 0.0 and (usable_energy - exp) < req_reserve:
                                    blocked = True
                                elif (usable_energy - exp) < home_needed and not discharge_arbitrage_ok:
                                    blocked = True
                        if not blocked and (override or nsi >= min_slot_idx):
                            _update(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)
                else:
                    min_ei = 1
                    max_ei = int(round(max_exp / energy_step))
                    for ei in range(min_ei, max_ei + 1):
                        exp = ei * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - exp) / energy_step))))
                        if not override and nsi < min_slot_idx:
                            continue
                        ac_exp = exp * config.eta_discharge
                        to_grid = max(0.0, ac_exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - ac_exp - pv_kwh)
                        if not override and to_grid > 0.0:
                            if exp < config.min_energy_to_discharge or not can_export:
                                continue
                            if is_pre_solar_low_price:
                                continue
                            req_reserve = (higher_sell_cap + home_needed) if (not discharge_arbitrage_ok and slot_hour < 12) else higher_sell_cap
                            if req_reserve > 0.0 and (usable_energy - exp) < req_reserve:
                                continue
                            if (usable_energy - exp) < home_needed and not discharge_arbitrage_ok:
                                continue
                        _update(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)

            # === PV_CHARGE: PV surplus -> battery, overflow -> grid ===
            if (not override_action or (mode_config and mode_config.charge_from_pv)) and pv_surplus > 0 and avail_cap >= energy_step and (target_nsi is None or target_nsi > state_idx):
                max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_charge_power *= remaining_hour_fraction
                max_pvc = min(pv_surplus, avail_cap, max_charge_power)
                
                if target_nsi is not None:
                    max_possible_chg_steps = int((max_pvc + 1e-6) / energy_step)
                    if max_possible_chg_steps == 0 and slot_idx == 1 and remaining_hour_fraction < 1.0 and pv_surplus > 0.001:
                        max_possible_chg_steps = 1
                    desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps)
                    min_ci = max(1, int(round(config.min_energy_to_discharge / energy_step))) if config.min_energy_to_discharge > 0 else 1
                    if desired_chg_steps >= min_ci:
                        chg = desired_chg_steps * energy_step
                        nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
                        reward = sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update(nsi, reward, ACT_PV_CHARGE, chg)
                elif override_action:
                    chg_steps = int((max_pvc + 1e-6) / energy_step)
                    if chg_steps == 0 and slot_idx == 1 and remaining_hour_fraction < 1.0 and pv_surplus > 0.001:
                        chg_steps = 1
                    if chg_steps >= 1:
                        chg = chg_steps * energy_step
                        nsi = min(max_energy_idx, max(0, state_idx + chg_steps))
                        reward = sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update(nsi, reward, ACT_PV_CHARGE, chg)
                else:
                    steps = int((max_pvc + 1e-6) / energy_step)
                    if steps == 0 and slot_idx == 1 and remaining_hour_fraction < 1.0 and pv_surplus > 0.001:
                        steps = 1
                    for ci in range(1, steps + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        reward = effective_sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update(nsi, reward, ACT_PV_CHARGE, chg)

            # === GRID_CHARGE: charge battery from grid ===
            min_gc_power = max(0.5, float(config.min_energy_to_discharge or 0.5))
            max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
            if (not override_action or (mode_config and mode_config.charge_from_grid)) and (avail_cap >= energy_step or (target_nsi is not None and target_nsi > state_idx) or (slot_idx == 1 and state_idx < max_energy_idx)) and (target_nsi is None or target_nsi > state_idx) and (override or max_charge_power >= min_gc_power or slot_idx == 1):
                if slot_idx == 1 and remaining_hour_fraction < 1.0:
                    max_charge_power *= remaining_hour_fraction
                max_gc = min(max_charge_power, max(energy_step, avail_cap)) if (target_nsi is not None and target_nsi > state_idx) else min(max_charge_power, avail_cap)

                if not override and config.min_arbitrage_profit > 0.0:
                    curr_slot = scaled_slots[slot_idx - 1]
                    is_pre_solar_night = curr_slot.get("pv_kwh", 0.0) <= 0.5 and curr_slot.get("hour", 0) in (22, 23, 0, 1, 2, 3, 4, 5, 6)
                    eta_rt = (config.eta_charge * config.eta_discharge) if (config.eta_charge > 0.0 and config.eta_discharge > 0.0) else 1.0
                    eff_stored_cost = (buy_price / eta_rt) + cycle_cost

                    if is_pre_solar_night:
                        pre_solar_slots = []
                        for _ps in scaled_slots[slot_idx - 1:]:
                            if _ps.get("pv_kwh", 0.0) > 0.5 or _ps.get("hour", 0) in range(10, 22):
                                break
                            pre_solar_slots.append(_ps)
                        sum_pv_ahead = sum(s.get("pv_kwh", 0.0) for s in scaled_slots[slot_idx - 1:])
                        max_pre_solar_sell = max((s.get("sell_price", 0.0) for s in pre_solar_slots), default=0.0)
                        cheaper_future_buy = any(s.get("buy_price", 0.0) < (buy_price - 0.01) for s in scaled_slots[slot_idx:])
                        max_all_sell = max((s.get("sell_price", 0.0) for s in scaled_slots[slot_idx - 1:]), default=0.0)
                        all_arb_margin = (max_all_sell * config.eta_discharge) - (buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price) - cycle_cost
                        eval_to_pre_solar = (
                            (max_pre_solar_sell >= config.min_discharge_price and all_arb_margin < config.min_arbitrage_profit)
                            or cheaper_future_buy
                            or sum_pv_ahead >= max(0.0, usable_capacity - usable_energy)
                        )
                        eval_slots = pre_solar_slots if eval_to_pre_solar else scaled_slots[slot_idx - 1:]

                        max_future_sell = max((s.get("sell_price", 0.0) for s in eval_slots), default=0.0)
                        effective_future_sell = max_future_sell * config.eta_discharge
                        effective_buy_cost = buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price
                        arbitrage_margin = effective_future_sell - effective_buy_cost - cycle_cost
                        if arbitrage_margin < config.min_arbitrage_profit:
                            home_deficit_until_solar = 0.0
                            for f_slot in pre_solar_slots:
                                if f_slot.get("buy_price", 0.0) > eff_stored_cost:
                                    f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                    net_def = max(0.0, f_cons - f_slot.get("pv_kwh", 0.0))
                                    home_deficit_until_solar += (net_def / config.eta_discharge if config.eta_discharge > 0.0 else net_def)
                            pre_solar_needed = max(0.0, max(home_deficit_until_solar, 1.0 if home_deficit_until_solar > 0.1 else 0.0) - usable_energy)
                            if not cheaper_future_buy:
                                sum_day_home_def = 0.0
                                sum_pv_ahead = 0.0
                                for f_slot in scaled_slots[slot_idx - 1:]:
                                    if f_slot.get("date") != curr_slot.get("date"):
                                        break
                                    f_pv = f_slot.get("pv_kwh", 0.0)
                                    f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                    if f_pv > f_cons:
                                        sum_pv_ahead += (f_pv - f_cons)
                                    else:
                                        sum_day_home_def += (f_cons - f_pv)
                                needed_day_deficit = max(0.0, sum_day_home_def - sum_pv_ahead - usable_energy)
                                space_with_solar_reserve = max(0.0, avail_cap - sum_pv_ahead)
                                allowed_capacity = max(0.0, min(space_with_solar_reserve, max(pre_solar_needed, needed_day_deficit)))
                            else:
                                allowed_capacity = max(0.0, pre_solar_needed)
                            max_gc = min(max_gc, allowed_capacity)
                    else:
                        future_slots = scaled_slots[slot_idx - 1:]
                        max_future_sell = max((s.get("sell_price", 0.0) for s in future_slots), default=0.0)
                        effective_future_sell = max_future_sell * config.eta_discharge
                        effective_buy_cost = buy_price / config.eta_charge if config.eta_charge > 0.0 else buy_price
                        arbitrage_margin = effective_future_sell - effective_buy_cost - cycle_cost
                        orig_slot = slots[slot_idx - 1]
                        orig_buy = orig_slot.get("buy_price", 0.0)
                        orig_max_future_sell = max((s.get("sell_price", 0.0) for s in slots[slot_idx - 1:]), default=0.0)
                        gross_arbitrage_margin = orig_max_future_sell - orig_buy
                        is_arb_profitable = (arbitrage_margin >= config.min_arbitrage_profit) or (gross_arbitrage_margin >= config.min_arbitrage_profit and arbitrage_margin > 0.0)
                        cheaper_future_buy = any(s.get("buy_price", 0.0) < (buy_price - 0.005) for s in future_slots[1:])
                        if not is_arb_profitable:
                            sum_expensive_deficit = 0.0
                            sum_pv_between = 0.0
                            cheaper_future_capacity = 0.0
                            for f_slot in future_slots[1:]:
                                f_buy = f_slot.get("buy_price", 0.0)
                                f_pv = f_slot.get("pv_kwh", 0.0)
                                f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("ev_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0)
                                if f_buy < (buy_price - 0.005):
                                    f_net_pv = max(0.0, f_pv - f_cons)
                                    cheaper_future_capacity += max(0.0, config.battery_max_charge_power - f_net_pv)
                                if f_pv > f_cons:
                                    sum_pv_between += (f_pv - f_cons)
                                elif f_buy > eff_stored_cost:
                                    net_def = f_cons - f_pv
                                    sum_expensive_deficit += (net_def / config.eta_discharge if config.eta_discharge > 0.0 else net_def)
                            reserve_to_charge = float(min_end_usable or 0.0) if buy_price <= (ref_night_buy + 0.15) else 0.0
                            total_deficit = max(0.0, sum_expensive_deficit + reserve_to_charge - sum_pv_between - usable_energy)
                            net_needed = max(0.0, total_deficit - cheaper_future_capacity)
                            max_future_buy = max((s.get("buy_price", 0.0) for s in future_slots[1:]), default=0.0)
                            eff_saving_vs_future_buy = max_future_buy * config.eta_discharge - eff_stored_cost
                            if eff_saving_vs_future_buy >= 0.15 or buy_price <= 0.25:
                                spare_capacity_after_solar = max(0.0, avail_cap - sum_pv_between)
                                home_saving_cap = max(0.0, min(spare_capacity_after_solar, sum_expensive_deficit - usable_energy))
                                net_needed = max(net_needed, home_saving_cap)
                            max_gc = min(max_gc, net_needed)
                        else:
                            # Arbitrage is profitable, but must not displace cheap solar!
                            # If future solar can be sold at a higher price than current charging cost,
                            # charging is allowed up to that profitable sale volume.
                            pv_cheap_to_reserve = 0.0
                            effective_charge_cost = effective_buy_cost + cycle_cost
                            for f_slot in future_slots[1:]:
                                f_sell = f_slot.get("sell_price", 0.0)
                                f_cons = f_slot.get("consumption_kwh", 0.0) + f_slot.get("planned_boiler_kwh", 0.0) + f_slot.get("ev_kwh", 0.0)
                                f_net_pv = max(0.0, f_slot.get("pv_kwh", 0.0) - f_cons)
                                if f_sell < effective_charge_cost:
                                    pv_cheap_to_reserve += f_net_pv
                                if f_sell >= max_future_sell - 1e-4:
                                    break
                            allowed_arbitrage_gc = max(0.0, avail_cap - pv_cheap_to_reserve)
                            max_gc = min(max_gc, allowed_arbitrage_gc)
                
                if target_nsi is not None:
                    max_possible_chg_steps = int(round(max_gc / energy_step))
                    if max_possible_chg_steps == 0 and slot_idx == 1 and target_nsi > state_idx:
                        max_possible_chg_steps = 1
                    desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps)
                    if desired_chg_steps >= 1:
                        chg = desired_chg_steps * energy_step
                        nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
                        grid_part = max(0.0, chg - pv_surplus)
                        net_chg = grid_part / config.eta_charge if config.eta_charge > 0.0 else grid_part
                        reward = - buy_price * (net_chg + pv_deficit)
                        if slot_idx == 1:
                            reward += 1e-4 * chg
                        _update(nsi, reward, ACT_GRID_CHARGE, chg)
                else:
                    min_ci = 1 if (slot_idx == 1 or avail_cap < config.min_energy_to_discharge) else (max(1, int(round(config.min_energy_to_discharge / energy_step))) if config.min_energy_to_discharge > 0 else 1)
                    for ci in range(min_ci, int(round(max_gc / energy_step)) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        grid_part = max(0.0, chg - pv_surplus)
                        net_chg = grid_part / config.eta_charge if config.eta_charge > 0.0 else grid_part
                        reward = - buy_price * (net_chg + pv_deficit)
                        if slot_idx == 1:
                            reward += 1e-4 * chg
                        _update(nsi, reward, ACT_GRID_CHARGE, chg)

            # === SELF_CONSUME: battery covers consumption deficit ===
            if (not override_action or (mode_config and mode_config.discharge_to_house)) and pv_deficit > 0.001 and usable_energy >= energy_step and (target_nsi is None or target_nsi < state_idx):
                needed_dc = pv_deficit / config.eta_discharge if config.eta_discharge > 0.0 else pv_deficit
                max_sc = min(usable_energy, needed_dc)
                
                if target_nsi is not None:
                    max_possible_sc_steps = max(1, int(round(max_sc / energy_step)))
                    desired_sc_steps = min(state_idx - target_nsi, max_possible_sc_steps)
                    if desired_sc_steps >= 1:
                        sc = desired_sc_steps * energy_step
                        nsi = max(0, min(max_energy_idx, state_idx - desired_sc_steps))
                        if override or nsi >= min_slot_idx:
                            ac_sc = sc * config.eta_discharge
                            remaining_deficit = max(0.0, pv_deficit - ac_sc)
                            _update(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)
                elif override_action:
                    sc_steps = max(1, int(round(max_sc / energy_step)))
                    sc = sc_steps * energy_step
                    nsi = max(0, min(max_energy_idx, state_idx - sc_steps))
                    ac_sc = sc * config.eta_discharge
                    remaining_deficit = max(0.0, pv_deficit - ac_sc)
                    _update(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)
                else:
                    max_sci = max(1, int(round(max_sc / energy_step)))
                    for sci in range(1, max_sci + 1):
                        sc = sci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - sc) / energy_step))))
                        if not override and nsi < min_slot_idx:
                            continue
                        ac_sc = sc * config.eta_discharge
                        remaining_deficit = max(0.0, pv_deficit - ac_sc)
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
        reserve_energy = min(usable_energy, min_end_usable)
        surplus_energy = max(0.0, usable_energy - min_end_usable)
        total_value = value + reserve_energy * terminal_value_per_kwh + surplus_energy * config.min_sell_price
        if total_value > best_total_value:
            best_total_value = total_value
            best_final_idx = state_idx

    if best_total_value == neg_inf:
        for state_idx, value in enumerate(dp[n_slots]):
            if value == neg_inf:
                continue
            usable_energy = state_idx * energy_step
            reserve_energy = min(usable_energy, min_end_usable)
            surplus_energy = max(0.0, usable_energy - min_end_usable)
            total_value = value + reserve_energy * terminal_value_per_kwh + surplus_energy * config.min_sell_price
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

    # --- DEBUG: log full DP trajectory after backtracking ---
    _ACT_NAMES = {
        ACT_SOL: "SOL",
        ACT_DIS: "DIS",
        ACT_PV_CHARGE: "PV_CHG",
        ACT_GRID_CHARGE: "GRID_CHG",
        ACT_SELF_CONSUME: "SELF_CON",
        ACT_PAID_IMPORT: "PAID_IMP",
    }
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _traj_usable = current_usable
        _traj_lines: list[str] = []
        for _t_slot, _t_act, _t_amt in zip(scaled_slots, types_by_slot, amounts_by_slot, strict=False):
            _t_soc = max(0.0, min(100.0, config.battery_min_soc + (_traj_usable / config.battery_capacity * 100.0)))
            _traj_lines.append(
                f"  {_t_slot.get('date')} {_t_slot.get('hour'):02d}h  "
                f"act={_ACT_NAMES.get(_t_act, str(_t_act)):<8}  "
                f"amt={_t_amt:+.2f}kWh  "
                f"SOC={_t_soc:.1f}%  "
                f"buy={_t_slot.get('buy_price', 0.0):.3f}  "
                f"sell={_t_slot.get('sell_price', 0.0):.3f}  "
                f"pv={_t_slot.get('pv_kwh', 0.0):.2f}kWh  "
                f"cons={_t_slot.get('consumption_kwh', 0.0):.2f}kWh"
            )
            # Advance simulated state for SOC display
            if _t_act == ACT_GRID_CHARGE:
                _traj_usable = min(usable_capacity, _traj_usable + _t_amt)
            elif _t_act == ACT_PV_CHARGE:
                _traj_usable = min(usable_capacity, _traj_usable + _t_amt)
            elif _t_act == ACT_DIS:
                _traj_usable = max(0.0, _traj_usable - _t_amt)
            elif _t_act == ACT_SELF_CONSUME:
                _traj_usable = max(0.0, _traj_usable - _t_amt)

        _gc_slots = [
            (scaled_slots[i].get("date"), scaled_slots[i].get("hour"),
             scaled_slots[i].get("buy_price", 0.0),
             scaled_slots[i].get("pv_kwh", 0.0),
             scaled_slots[i].get("consumption_kwh", 0.0),
             amounts_by_slot[i])
            for i in range(n_slots) if types_by_slot[i] == ACT_GRID_CHARGE
        ]
        _gc_summary = "  " + "\n  ".join(
            f"{d} {h:02d}h  buy={bp:.3f}  pv={pv:.2f}  cons={cons:.2f}  "
            f"net_from_grid≈{max(0.0, amt - max(0.0, pv - cons)):.2f}kWh  "
            f"total_chg={amt:.2f}kWh"
            for d, h, bp, pv, cons, amt in _gc_slots
        ) if _gc_slots else "  (none)"

        _LOGGER.debug(
            "EMS DP trajectory (best_value=%.4f, end_SOC=%.1f%%):\n%s\n"
            "GRID_CHARGE slots:\n%s",
            best_total_value,
            max(0.0, min(100.0, config.battery_min_soc + (best_final_idx * energy_step / config.battery_capacity * 100.0))),
            "\n".join(_traj_lines),
            _gc_summary,
        )
    # --- END DEBUG ---

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

    # Compute last discharge hour in [12..23] per date for dynamic morning min SOC
    _lsh: dict[str, int] = {}
    for _ls_slot, _ls_act in zip(scaled_slots, types_by_slot, strict=False):
        if _ls_act == ACT_DIS:
            _ls_date = _ls_slot.get("date", "")
            _ls_hour = _ls_slot.get("hour", 0)
            if _ls_hour >= 12:
                if _ls_date not in _lsh or _ls_hour > _lsh[_ls_date]:
                    _lsh[_ls_date] = _ls_hour
    config.last_sell_hour_by_date = _lsh

    for slot, act, amount in zip(scaled_slots, types_by_slot, amounts_by_slot, strict=False):
        start_usable = usable_energy
        soc_val = max(0.0, min(100.0, config.battery_min_soc + (start_usable / config.battery_capacity * 100.0)))
        expected_trajectory.append(round(soc_val, 2))

        # Apply post-processing filter for small grid discharges (exporters)
        if act == ACT_DIS and amount > 0 and not slot.get("override"):
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)
            home_deficit = max(0.0, total_consumption - slot["pv_kwh"])
            battery_to_home = min(amount, home_deficit)
            battery_to_grid = max(0.0, amount - battery_to_home)

            min_grid_exp = max(0.15, config.min_energy_to_discharge)
            # For the current running slot, normalize export to full-hour equivalent so end-of-hour minutes don't kill discharge
            is_first_running_slot = (slot is scaled_slots[0] and remaining_hour_fraction < 1.0)
            effective_grid_exp = (battery_to_grid / remaining_hour_fraction) if is_first_running_slot else battery_to_grid

            if effective_grid_exp < min_grid_exp:
                # If grid export portion is below limit, switch to self-consumption or idle
                amount = round(amount, 2)
                if home_deficit > 0.0:
                    act = ACT_SELF_CONSUME
                else:
                    act = ACT_SOL
                    amount = 0.0

        # Apply post-processing filter for negligible grid charges.
        # When net energy drawn from the grid is < 0.25 kWh (or battery is >= 95% SOC),
        # the slot is effectively a PV-only charge — downgrade GRID_CHARGE to
        # PV_CHARGE (or SOL if there is no PV surplus at all).
        if act == ACT_GRID_CHARGE and amount > 0 and not slot.get("override"):
            pv_surplus = max(0.0, slot["pv_kwh"] - (slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)))
            net_grid = amount - pv_surplus
            if net_grid < 0.25 or soc_val >= 95.0:
                if pv_surplus > 0.0:
                    amount = round(pv_surplus, 2)
                    act = ACT_PV_CHARGE
                    _LOGGER.debug(
                        "EMS DP: GRID_CHG→PV_CHG at %s %02dh "
                        "(net_grid=%.3f kWh < 0.25 threshold or soc=%.1f%% >= 95%%, pv_surplus=%.3f kWh)",
                        slot.get("date"), slot.get("hour"), net_grid, soc_val, pv_surplus,
                    )
                else:
                    act = ACT_SOL
                    amount = 0.0
                    _LOGGER.debug(
                        "EMS DP: GRID_CHG→SOL at %s %02dh "
                        "(net_grid=%.3f kWh < 0.25 threshold or soc=%.1f%% >= 95%%, no pv_surplus)",
                        slot.get("date"), slot.get("hour"), net_grid, soc_val,
                    )

        if act == ACT_DIS and amount > 0:
            end_usable = usable_energy - amount
            total_battery_discharge += amount
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)
            home_deficit = max(0.0, total_consumption - slot["pv_kwh"])
            battery_to_home = min(amount, home_deficit)
            battery_to_grid = max(0.0, amount - battery_to_home)
            grid_import = max(0.0, home_deficit - amount)
            total_export += battery_to_grid
            slot_min_soc = config.get_min_soc(slot.get('hour', 0), slot.get('date'))
            soc_limit = max(
                slot_min_soc,
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
                "override": bool(slot.get("override")),
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
                "override": bool(slot.get("override")),
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
                "override": bool(slot.get("override")),
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
                "override": bool(slot.get("override")),
            })
            usable_energy = end_usable

        elif act == ACT_PAID_IMPORT:
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0) + slot.get("planned_boiler_kwh", 0.0)
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
