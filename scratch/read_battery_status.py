import json
import os

restore_path = r"\\192.168.100.5\config\.storage\core.restore_state"
if os.path.exists(restore_path):
    with open(restore_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    states = data.get("data", [])
    entities_to_check = [
        "sensor.inverter_battery_capacity",
        "sensor.inverter_battery",
        "sensor.inverter_battery_power",
        "sensor.inverter_battery_voltage",
        "sensor.inverter_load_power",
        "sensor.inverter_pv_power",
        "sensor.solcast_pv_forecast_forecast_today",
        "sensor.solcast_pv_forecast_forecast_tomorrow"
    ]
    for state_data in states:
        state_info = state_data.get("state", {})
        entity_id = state_info.get("entity_id")
        if entity_id in entities_to_check:
            print(f"Entity: {entity_id}")
            print(f"  State: {state_info.get('state')}")
            attrs = state_info.get("attributes", {})
            for k, v in attrs.items():
                if k in ("unit_of_measurement", "friendly_name") or "forecast" in k:
                    print(f"    {k}: {v}")
            print("-" * 50)
else:
    print("Restore state file not found.")
