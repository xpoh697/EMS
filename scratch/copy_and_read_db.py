import shutil
import sqlite3
import json
import os

db_src = r"\\192.168.100.5\config\home-assistant_v2.db"
wal_src = r"\\192.168.100.5\config\home-assistant_v2.db-wal"
shm_src = r"\\192.168.100.5\config\home-assistant_v2.db-shm"

db_dst = "local_ha.db"
wal_dst = "local_ha.db-wal"
shm_dst = "local_ha.db-shm"

# Copy files
shutil.copy2(db_src, db_dst)
if os.path.exists(wal_src):
    shutil.copy2(wal_src, wal_dst)
if os.path.exists(shm_src):
    shutil.copy2(shm_src, shm_dst)

# Connect to local copy
conn = sqlite3.connect(db_dst)
cursor = conn.cursor()

entities = ["sensor.dp", "sensor.boiler_dp"]
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
            schedule = attrs.get('schedule', attrs.get('current_plan', []))
            print(f"  Schedule items:")
            for item in schedule[:24]:
                print(f"    Hour: {item.get('hour')} | Buy: {item.get('buy_price')} Sell: {item.get('sell_price')} | Action: {item.get('action')} | PhysMode: {item.get('physical_mode')} | SOC: {item.get('soc', item.get('expected_soc'))}%")
        else:
            print(f"  Attributes: {attrs}")
    else:
        print(f"Entity: {entity} - NOT FOUND")
    print("-" * 50)

conn.close()

# Cleanup
for f in (db_dst, wal_dst, shm_dst):
    if os.path.exists(f):
        try:
            os.remove(f)
        except Exception:
            pass
