import json
import os

debug_slots_path = r"\\192.168.100.5\config\ems_debug_slots.json"
if os.path.exists(debug_slots_path):
    with open(debug_slots_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("Type of ems_debug_slots:", type(data))
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                print(f"Key: {k} (list of length {len(v)})")
                if len(v) > 0:
                    print(f"  First item: {v[0]}")
            else:
                print(f"Key: {k} = {v}")
    elif isinstance(data, list):
        print(f"List of length {len(data)}")
        for idx, item in enumerate(data[:10]):
            print(f"  Item {idx}: Hour {item.get('hour')} Action {item.get('action')} PhysMode {item.get('physical_mode')} SOC {item.get('soc')}%")
else:
    print("Debug slots file not found.")
