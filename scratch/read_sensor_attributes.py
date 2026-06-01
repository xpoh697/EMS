import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

entities = [
    "sensor.dp",
    "sensor.boiler_dp"
]

for entity in entities:
    cursor.execute("""
        SELECT s.state, s.attributes 
        FROM states s
        JOIN states_meta m ON s.metadata_id = m.metadata_id
        WHERE m.entity_id = ?
        ORDER BY s.last_updated_ts DESC
        LIMIT 1
    """, (entity,))
    row = cursor.fetchone()
    if row:
        state, attrs_json = row
        try:
            attrs = json.loads(attrs_json)
        except Exception:
            attrs = attrs_json
        print(f"Entity: {entity}")
        print(f"  State: {state}")
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if k not in ('schedule', 'stats'):
                    print(f"    {k}: {v}")
                elif k == 'schedule':
                    print(f"    schedule length: {len(v)}")
                    if len(v) > 0:
                        print(f"      first 3 slots: {v[:3]}")
        else:
            print(f"  Attributes: {attrs}")
    else:
        print(f"Entity: {entity} - NOT FOUND")
    print("-" * 50)
