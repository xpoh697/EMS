import sqlite3
import json
import sys

# Set standard output encoding to utf-8
sys.stdout.reconfigure(encoding='utf-8')

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

entities = [
    "sensor.boiler_dp",
    "select.ems_boiler_mode",
    "sensor.boiler_water_temperature",
    "climate.mosquitto_broker_diyless_dhw",
    "switch.152832116785770_power",
    "switch.1st_power_plug_boiler_pump",
    "input_boolean.boiler_used",
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
        if isinstance(attrs, dict):
            summary = {k: v for k, v in attrs.items() if k not in ['schedule', 'hourly_forecast', 'baseline', 'calibration_data']}
            print(f"  Attributes summary: {json.dumps(summary, indent=2, ensure_ascii=False)}")
        else:
            print(f"  Attributes: {attrs}")
    else:
        print(f"Entity: {entity} - NOT FOUND")
    print("-" * 50)

conn.close()
