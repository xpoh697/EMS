import sqlite3
import json

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

entities = [
    "sensor.boiler_dp",
    "sensor.dp",
    "sensor.boiler_calibration",
    "sensor.ems_diagnostic",
    "select.ems_boiler_mode",
    "sensor.elec_boiler_temp",
    "climate.gas_boiler",
    "switch.elec_boiler_heater",
    "switch.boiler_pump",
    "switch.boiler_bypass",
]

for entity in entities:
    cursor.execute("""
        SELECT s.state, s.attributes, s.last_updated_ts 
        FROM states s 
        JOIN states_meta m ON s.metadata_id = m.metadata_id 
        WHERE m.entity_id = ? 
        ORDER BY s.state_id DESC LIMIT 1
    """, (entity,))
    res = cursor.fetchone()
    if res:
        state, attributes, ts = res
        try:
            attrs = json.loads(attributes)
        except Exception:
            attrs = attributes
        print(f"Entity: {entity}")
        print(f"  State: {state}")
        # Print only a summary of attributes to avoid too much verbosity
        if isinstance(attrs, dict):
            summary = {k: v for k, v in attrs.items() if k not in ['schedule', 'hourly_forecast', 'baseline', 'calibration_data']}
            print(f"  Attributes summary: {json.dumps(summary, indent=2, ensure_ascii=False)}")
            if 'schedule' in attrs:
                print(f"  Schedule length: {len(attrs['schedule'])}")
                if len(attrs['schedule']) > 0:
                    print("  First 3 slots in schedule:")
                    for slot in attrs['schedule'][:3]:
                        print(f"    Hour {slot.get('hour')}: mode={slot.get('mode')} temp={slot.get('temp_start')}->{slot.get('temp_end')} bypass={slot.get('bypass')} cost={slot.get('cost')}")
        else:
            print(f"  Attributes: {attrs}")
    else:
        print(f"Entity: {entity} - NOT FOUND")
    print("-" * 50)

conn.close()
