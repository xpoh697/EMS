import json

restore_path = r"\\192.168.100.5\config\.storage\core.restore_state"
try:
    with open(restore_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    states_list = data.get("data", [])
    for state_item in states_list:
        state_info = state_item.get("state", {})
        entity_id = state_info.get("entity_id")
        if entity_id == "sensor.boiler_dp":
            attrs = state_info.get("attributes", {})
            schedule = attrs.get("schedule", [])
            print(f"Total schedule slots: {len(schedule)}")
            for slot in schedule:
                print(f"Hour {slot.get('hour'):02d}: mode={slot.get('mode'):9s} gas={slot.get('temp_gas_start')}->{slot.get('temp_gas_end')} elec={slot.get('temp_elec_start')}->{slot.get('temp_elec_end')} active={slot.get('temp_active_start')}->{slot.get('temp_active_end')} bypass={str(slot.get('bypass')):5s} cost={slot.get('cost')}")
except Exception as e:
    print("Error:", e)
