import sys
import os

capacity = 5.12
min_bat_soc = 20.0
usable_capacity = capacity * (1 - min_bat_soc / 100.0) # 4.096
energy_step = 0.1
max_energy_idx = int(round(usable_capacity / energy_step)) # 41

# Precompute CVCC multipliers
def get_cvcc(soc):
    if soc < 93.0: return 1.0
    if soc < 95.0: return 0.80
    if soc < 97.0: return 0.50
    if soc < 99.0: return 0.25
    return 0.10

cvcc_multipliers = [1.0] * (max_energy_idx + 1)
for state in range(max_energy_idx + 1):
    clamped_soc = min_bat_soc + (state * energy_step / capacity) * 100.0
    cvcc_multipliers[state] = get_cvcc(clamped_soc)

battery_max_charge_power = 6.6
battery_max_discharge_power = 3.0

# Test targets
target_socs = [20.0, 35.0, 54.5, 59.3, 59.5, 80.0, 100.0]

print("--- Testing Charging Override Fix ---")
mismatch_count = 0
for target_soc in target_socs:
    target_usable = capacity * (target_soc - min_bat_soc) / 100.0
    target_nsi = max(0, min(max_energy_idx, int(round(target_usable / energy_step))))
    
    for state_idx in range(max_energy_idx + 1):
        if target_nsi <= state_idx:
            continue
            
        # Expected nsi calculation (PROPOSED)
        max_charge_power = battery_max_charge_power * cvcc_multipliers[state_idx]
        usable_energy = state_idx * energy_step
        avail_cap = usable_capacity - usable_energy
        max_gc = min(max_charge_power, avail_cap)
        max_possible_chg_steps_expected = int(max_gc / energy_step)
        expected_nsi = min(target_nsi, state_idx + max_possible_chg_steps_expected)
        
        # Grid Charge calculation inside dp_engine.py
        max_gc_act = min(max_charge_power, avail_cap)
        max_possible_chg_steps_actual = int(max_gc_act / energy_step)
        desired_chg_steps = min(target_nsi - state_idx, max_possible_chg_steps_actual)
        actual_nsi = min(max_energy_idx, max(0, state_idx + desired_chg_steps))
        
        if actual_nsi != expected_nsi:
            mismatch_count += 1
            print(f"  MISMATCH at target={target_soc}%, state_idx={state_idx} (SOC={min_bat_soc + (state_idx*energy_step/capacity)*100.0:.1f}%)")
            print(f"    expected_nsi={expected_nsi}")
            print(f"    actual_nsi={actual_nsi}")

print(f"Charging test completed with {mismatch_count} mismatches.")

print("\n--- Testing Discharging Override Fix ---")
mismatch_count_dis = 0
for target_soc in target_socs:
    target_usable = capacity * (target_soc - min_bat_soc) / 100.0
    target_nsi = max(0, min(max_energy_idx, int(round(target_usable / energy_step))))
    
    for state_idx in range(max_energy_idx + 1):
        if target_nsi >= state_idx:
            continue
            
        # Expected nsi calculation (PROPOSED)
        usable_energy = state_idx * energy_step
        max_discharge_power = battery_max_discharge_power
        max_exp = min(max_discharge_power, usable_energy)
        max_possible_dis_steps_expected = int(max_exp / energy_step)
        expected_nsi = max(target_nsi, state_idx - max_possible_dis_steps_expected)
        
        # Discharging calculation inside dp_engine.py
        max_exp_act = min(max_discharge_power, usable_energy)
        max_possible_dis_steps_actual = int(max_exp_act / energy_step)
        desired_dis_steps = min(state_idx - target_nsi, max_possible_dis_steps_actual)
        actual_nsi = min(max_energy_idx, max(0, state_idx - desired_dis_steps))
        
        if actual_nsi != expected_nsi:
            mismatch_count_dis += 1
            print(f"  MISMATCH at target={target_soc}%, state_idx={state_idx} (SOC={min_bat_soc + (state_idx*energy_step/capacity)*100.0:.1f}%)")
            print(f"    expected_nsi={expected_nsi}")
            print(f"    actual_nsi={actual_nsi}")

print(f"Discharging test completed with {mismatch_count_dis} mismatches.")
