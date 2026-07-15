import sqlite3
import os

db_path = r'\\192.168.100.5\config\home-assistant_v2.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT entity_id FROM states_meta")
    all_entities = [r[0] for r in cursor.fetchall()]
    print(f"Total entities in states_meta: {len(all_entities)}")
    print("Entities containing 'dp' or 'boiler' or 'ems':")
    for e in all_entities:
        if 'dp' in e or 'boiler' in e or 'ems' in e:
            print(" ", e)
    conn.close()
else:
    print("home-assistant_v2.db not found")
