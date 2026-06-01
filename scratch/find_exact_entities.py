import json
import os

restore_path = r"\\192.168.100.5\config\.storage\core.restore_state"
if os.path.exists(restore_path):
    with open(restore_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    states = data.get("data", [])
    print("Total states in restore_state:", len(states))
    for state_data in states:
        state_info = state_data.get("state", {})
        entity_id = state_info.get("entity_id")
        if entity_id == "sensor.scheduler":
            print(f"Entity: {entity_id}")
            print(f"  State: {state_info.get('state')}")
            attrs = state_info.get("attributes", {})
            for k, v in attrs.items():
                if k == "schedule":
                    print(f"    schedule length: {len(v)}")
                    for item in v:
                        print(f"      Hour {item.get('hour')}: Buy={item.get('buy_price')} Sell={item.get('sell_price')} PV={item.get('pv_kwh')} Cons={item.get('consumption_kwh')} Action={item.get('action')} PhysMode={item.get('physical_mode')} SOC={item.get('expected_soc')}%")
            print("-" * 50)
else:
    print("Restore state file not found.")
