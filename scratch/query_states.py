import sqlite3

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE '%boiler%' OR entity_id LIKE '%ems%'")
print("Matching entities:", [r[0] for r in cursor.fetchall()])

cursor.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE 'sensor.dp%'")
print("DP entities:", [r[0] for r in cursor.fetchall()])

conn.close()
