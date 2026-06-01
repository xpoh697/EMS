import json
import os

debug_slots_path = r"\\192.168.100.5\config\ems_debug_slots.json"
if os.path.exists(debug_slots_path):
    with open(debug_slots_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    slots_passed = data.get("slots_passed", [])
    print(f"Timestamp of log: {data.get('timestamp')}")
    print(f"Total slots: {len(slots_passed)}")
    for idx, slot in enumerate(slots_passed):
        print(f"Slot {idx:02d}: {slot.get('date')} H{slot.get('hour'):02d} | Buy: {slot.get('buy_price'):.4f} Sell: {slot.get('sell_price'):.4f} PV: {slot.get('pv_kwh'):.4f} Cons: {slot.get('consumption_kwh'):.4f} Boiler: {slot.get('planned_boiler_kwh'):.4f} | Action: {slot.get('action')} PhysMode: {slot.get('physical_mode')} ExpSOC: {slot.get('expected_soc')}%")
else:
    print("Debug slots file not found.")
