import json
import os

restore_path = r"\\192.168.100.5\config\.storage\core.restore_state"
if os.path.exists(restore_path):
    with open(restore_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    states = data.get("data", [])
    for state_data in states:
        state_info = state_data.get("state", {})
        entity_id = state_info.get("entity_id")
        if entity_id == "input_select.inverter_work_mode":
            print(f"Entity: {entity_id}")
            print(f"  State: {state_info.get('state')}")
            attrs = state_info.get("attributes", {})
            for k, v in attrs.items():
                print(f"    {k}: {v}")
            print("-" * 50)
else:
    print("Restore state file not found.")
