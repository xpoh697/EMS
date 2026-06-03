import json
import os

debug_slots_path = r"\\192.168.100.5\config\ems_debug_slots.json"
if os.path.exists(debug_slots_path):
    with open(debug_slots_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    print("=== Boiler DP Debug Info ===")
    print("t_gas:", data.get("t_gas"))
    print("t_elec:", data.get("t_elec"))
    print("t_min:", data.get("t_min"))
    print("t_max_elec:", data.get("t_max_elec"))
    print("t_max_gas:", data.get("t_max_gas"))
    print("vol_elec:", data.get("vol_elec"))
    print("vol_gas:", data.get("vol_gas"))
    
    slots = data.get("slots", [])
    print("\nSlots:")
    for s in slots:
        print(f"  Hour {s.get('hour'):02d} | PV: {s.get('pv_kwh'):.2f} kWh | Cons: {s.get('consumption_kwh'):.2f} kWh | PhysMode: {s.get('physical_mode')} | Action: {s.get('action')}")
else:
    print("Debug slots file not found.")
