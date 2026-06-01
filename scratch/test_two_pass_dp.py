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
    if action == "grid_charge":
        return "buy", "override"
    if action == "sale_pv":
        return "sale_pv", "override"
    return "stop_sale", "fallback"

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

# We will redefine run_unified_dp to test two-pass planning
def run_unified_dp_two_pass(
    slots,
    current_usable,
    usable_capacity,
    cycle_cost,
    terminal_value_per_kwh,
    min_end_usable,
    config,
    remaining_hour_fraction = 1.0,
):
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
    ACT_SOL = 0
    ACT_DIS = 1
    ACT_PV_CHARGE = 2
    ACT_GRID_CHARGE = 3
    ACT_SELF_CONSUME = 4
    ACT_PAID_IMPORT = 5

    # Pre-calculate CVCC charge multipliers for all states to optimize loop performance
    cvcc_multipliers = []
    get_cvcc_charge_multiplier = context['get_cvcc_charge_multiplier']
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

    if first_override_slot_idx <= n_slots:
        dp_n = [[neg_inf] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_s_n = [[-1] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_t_n = [[ACT_SOL] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        prev_a_n = [[0.0] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
        dp_n[0][initial_idx] = 0.0

        for slot_idx, slot in enumerate(scaled_slots, start=1):
            sell_price = slot.get("sell_price", 0.0)
            buy_price = slot.get("buy_price", 0.0)
            pv_kwh = slot.get("pv_kwh", 0.0)
            consumption_kwh = slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0)
            pv_surplus = max(0.0, pv_kwh - consumption_kwh)
            pv_deficit = max(0.0, consumption_kwh - pv_kwh)

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
                _update_n(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

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
                        to_grid = max(0.0, exp + pv_kwh - consumption_kwh)
                        grid_imp = max(0.0, consumption_kwh - exp - pv_kwh)
                        _update_n(nsi, sell_price * to_grid - cycle_cost * exp - buy_price * grid_imp, ACT_DIS, exp)

                # === PV_CHARGE ===
                avail_cap = usable_capacity - usable_energy
                if pv_surplus > 0 and avail_cap >= energy_step:
                    max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    max_pvc = min(pv_surplus, avail_cap, max_charge_power)
                    for ci in range(1, int(max_pvc / energy_step) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        reward = sell_price * max(0.0, pv_surplus - chg) - buy_price * pv_deficit
                        reward += 1e-6 * chg
                        _update_n(nsi, reward, ACT_PV_CHARGE, chg)

                # === GRID_CHARGE ===
                if avail_cap >= energy_step:
                    max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    max_gc = min(max_charge_power, avail_cap)
                    for ci in range(1, int(max_gc / energy_step) + 1):
                        chg = ci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy + chg) / energy_step))))
                        _update_n(nsi, sell_price * pv_surplus - buy_price * (chg + pv_deficit) - cycle_cost * chg, ACT_GRID_CHARGE, chg)

                # === SELF_CONSUME ===
                if pv_deficit >= energy_step and usable_energy >= energy_step:
                    max_sc = min(usable_energy, pv_deficit)
                    for sci in range(1, int(round(max_sc / energy_step)) + 1):
                        sc = sci * energy_step
                        nsi = min(max_energy_idx, max(0, int(round((usable_energy - sc) / energy_step))))
                        remaining_deficit = max(0.0, pv_deficit - sc)
                        _update_n(nsi, -buy_price * remaining_deficit, ACT_SELF_CONSUME, sc)

                # === PAID_IMPORT ===
                if buy_price < 0 and consumption_kwh >= energy_step:
                    _update_n(state_idx, -buy_price * consumption_kwh, ACT_PAID_IMPORT, 0.0)

                if not state_updated:
                    _update_n(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

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
            total_value = value + usable_energy * terminal_value_per_kwh
            if total_value > best_total_value_normal:
                best_total_value_normal = total_value
                best_final_idx_normal = state_idx

        if best_total_value_normal == neg_inf:
            for state_idx, value in enumerate(dp_normal[n_slots]):
                if value == neg_inf:
                    continue
                usable_energy = state_idx * energy_step
                total_value = value + usable_energy * terminal_value_per_kwh
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

    # 3. Second Pass: run DP with overrides.
    # For slots before first override, restrict the DP state to ONLY the normal optimal state.
    dp = [[neg_inf] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
    prev_state = [[-1] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
    prev_type = [[ACT_SOL] * (max_energy_idx + 1) for _ in range(n_slots + 1)]
    prev_amount = [[0.0] * (max_energy_idx + 1) for _ in range(n_slots + 1)]

    dp[0][initial_idx] = 0.0

    for slot_idx, slot in enumerate(scaled_slots, start=1):
        if slot_idx < first_override_slot_idx:
            # Enforce exact normal trajectory boundary state
            if optimal_states_normal is not None:
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
        consumption_kwh = slot.get("consumption_kwh", 0.0) + slot.get("ev_kwh", 0.0)
        pv_surplus = max(0.0, pv_kwh - consumption_kwh)
        pv_deficit = max(0.0, consumption_kwh - pv_kwh)
        override = slot.get("override")
        
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

        mode_config = None
        if override_action:
            cheap_ahead = False
            if override_action != "self_consume":
                current_idx = slot_idx - 1
                horizon_end = min(current_idx + 7, len(scaled_slots))
                for f_idx in range(current_idx + 1, horizon_end):
                    future_p_buy = scaled_slots[f_idx].get("buy_price", 99.0)
                    if future_p_buy < 0.0:
                        cheap_ahead = True
                        break

            physical_mode, _ = map_dp_to_physical(
                action=override_action,
                sell_price=sell_price,
                pv_kwh=pv_kwh,
                min_sell_price=config.min_sell_price,
                min_discharge_price=config.min_discharge_price,
                cheap_ahead=cheap_ahead,
            )
            mode_config = INVERTER_MODES.get(physical_mode)

        for state_idx, current_value in enumerate(dp[slot_idx - 1]):
            if current_value == neg_inf:
                continue

            usable_energy = state_idx * energy_step
            state_updated = False

            expected_nsi = state_idx
            if target_nsi is not None:
                if target_nsi > state_idx:
                    max_charge_power = config.battery_max_charge_power * cvcc_multipliers[state_idx]
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_charge_power *= remaining_hour_fraction
                    avail_cap = usable_capacity - usable_energy
                    max_gc = min(max_charge_power, avail_cap)
                    max_possible_chg_steps = int(max_gc / energy_step)
                    expected_nsi = min(target_nsi, state_idx + max_possible_chg_steps)
                elif target_nsi < state_idx:
                    max_discharge_power = config.battery_max_discharge_power
                    if slot_idx == 1 and remaining_hour_fraction < 1.0:
                        max_discharge_power *= remaining_hour_fraction
                    max_exp = min(max_discharge_power, usable_energy)
                    max_possible_dis_steps = int(max_exp / energy_step)
                    expected_nsi = max(target_nsi, state_idx - max_possible_dis_steps)

            def _update(nsi: int, rwd: float, act: int, amt: float) -> None:
                nonlocal state_updated
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

            # === SOL ===
            if (not override_action or (mode_config and mode_config.name in ("idle", "sale_pv", "sale_pv_bat", "sale_pv_no_bat", "stop_sale", "no_pv_sale_no_bat"))) and (target_nsi is None or target_nsi == state_idx):
                is_sol_allowed = True
                if mode_config:
                    if mode_config.charge_from_pv and pv_surplus > 0 and avail_cap >= energy_step:
                        is_sol_allowed = False
                    elif mode_config.discharge_to_house and pv_deficit >= energy_step and usable_energy >= energy_step:
                        is_sol_allowed = False
                if is_sol_allowed:
                    _update(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

            # === DIS ===
            if ((not override_action and (not config.disable_discharge and sell_price >= config.min_discharge_price and sell_price > 0)) or (mode_config and mode_config.discharge_to_grid)) and (target_nsi is None or target_nsi < state_idx):
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

            # === PV_CHARGE ===
            avail_cap = usable_capacity - usable_energy
            if (not override_action or (mode_config and mode_config.charge_from_pv)) and pv_surplus > 0 and avail_cap >= energy_step and (target_nsi is None or target_nsi > state_idx):
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

            # === GRID_CHARGE ===
            if (not override_action or (mode_config and mode_config.charge_from_grid)) and avail_cap >= energy_step and (target_nsi is None or target_nsi > state_idx):
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

            # === SELF_CONSUME ===
            if (not override_action or (mode_config and mode_config.discharge_to_house)) and pv_deficit >= energy_step and usable_energy >= energy_step and (target_nsi is None or target_nsi < state_idx):
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

            # === PAID_IMPORT ===
            if not override_action and buy_price < 0 and consumption_kwh >= energy_step:
                _update(state_idx, -buy_price * consumption_kwh, ACT_PAID_IMPORT, 0.0)

            if not state_updated:
                _update(state_idx, sell_price * pv_surplus - buy_price * pv_deficit, ACT_SOL, 0.0)

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
    charge_hours = []
    discharge_hours = []
    pv_charge_hours = []
    self_consume_hours = []
    paid_import_hours = []
    usable_energy = current_usable
    total_export = 0.0
    total_battery_discharge = 0.0
    total_grid_charge = 0.0
    total_paid_import = 0.0
    expected_trajectory = []

    for slot, act, amount in zip(scaled_slots, types_by_slot, amounts_by_slot, strict=False):
        start_usable = usable_energy
        soc_val = max(0.0, min(100.0, config.battery_min_soc + (start_usable / config.battery_capacity * 100.0)))
        expected_trajectory.append(round(soc_val, 2))

        if act == ACT_DIS and amount > 0:
            end_usable = usable_energy - amount
            total_battery_discharge += amount
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0)
            home_deficit = max(0.0, total_consumption - slot["pv_kwh"])
            battery_to_home = min(amount, home_deficit)
            battery_to_grid = max(0.0, amount - battery_to_home)
            grid_import = max(0.0, home_deficit - amount)
            total_export += battery_to_grid
            discharge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "planned_energy_kwh": round(amount, 2),
            })
            usable_energy = end_usable
        elif act == ACT_PV_CHARGE and amount > 0:
            end_usable = min(usable_capacity, usable_energy + amount)
            pv_charge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "charge_kwh": round(amount, 2),
            })
            usable_energy = end_usable
        elif act == ACT_GRID_CHARGE and amount > 0:
            end_usable = min(usable_capacity, usable_energy + amount)
            total_grid_charge += amount
            charge_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "planned_energy_kwh": round(amount, 2),
            })
            usable_energy = end_usable
        elif act == ACT_SELF_CONSUME and amount > 0:
            end_usable = max(0.0, usable_energy - amount)
            self_consume_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
                "planned_energy_kwh": round(amount, 2),
            })
            usable_energy = end_usable
        elif act == ACT_PAID_IMPORT:
            total_consumption = slot["consumption_kwh"] + slot.get("ev_kwh", 0.0)
            total_paid_import += total_consumption
            paid_import_hours.append({
                "date": slot["date"],
                "hour": slot["hour"],
            })

    return charge_hours, discharge_hours, pv_charge_hours, self_consume_hours, paid_import_hours, {
        "slot_count": n_slots,
        "initial_usable": round(current_usable, 2),
        "expected_trajectory": expected_trajectory,
    }

buy_prices = [0.948287, 0.87814, 0.85739, 0.849899, 0.849887, 0.885606, 1.380878, 1.340165, 1.204582, 0.807083, 0.682853, 0.68273, 0.680393, 0.673689, 0.673689, 0.257162, 0.260852, 0.883355, 1.23024, 1.444223, 1.593324, 1.568453, 1.044399, 0.958607]
sell_prices = [0.687447, 0.6173, 0.59655, 0.589059, 0.589047, 0.624766, 0.698025, 0.657312, 0.521729, 0.12423, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2e-05, 0.200502, 0.547387, 0.76137, 0.910471, 0.8856, 0.783559, 0.697767]

current_hour = 9
slots = []
for h in range(current_hour, 24):
    override = None
    if h == 11:
        override = "grid_charge:70.0"
    slots.append({
        "date": "2026-05-27",
        "hour": h,
        "buy_price": buy_prices[h],
        "sell_price": sell_prices[h],
        "pv_kwh": 0.0, # no PV to force grid charging
        "consumption_kwh": 0.0,
        "override": override
    })

capacity = 14.0
min_bat_soc = 15.0
soc = 22.0

usable_capacity = capacity * (1 - min_bat_soc / 100.0)
current_usable = capacity * (soc - min_bat_soc) / 100.0

DPConfig = context['DPConfig']

dp_config = DPConfig(
    min_sell_price=0.1,
    min_discharge_price=0.0,
    battery_max_discharge_power=3.0, # 3.0 kW to limit charge per hour
    battery_max_charge_power=3.0,
    battery_min_soc=int(min_bat_soc),
    battery_capacity=capacity,
    min_energy_to_discharge=0.0,
    disable_discharge=False,
)

chg_h, dis_h, pvc_h, sc_h, pim_h, stats = run_unified_dp_two_pass(
    slots=slots,
    current_usable=current_usable,
    usable_capacity=usable_capacity,
    cycle_cost=0.089,
    terminal_value_per_kwh=0.33,
    min_end_usable=0.0,
    config=dp_config,
    remaining_hour_fraction=1.0,
)

print(f"Stats: {stats}")
print("\nSchedule plan:")
for idx, item in enumerate(stats.get("expected_trajectory", [])):
    slot = slots[idx]
    print(f"Hour {slot['hour']}: Start SOC={item}%, PlanAction={slot.get('override') or 'optimized'}")
