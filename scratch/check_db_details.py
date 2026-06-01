import os
import sqlite3

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM states_meta")
print("Total states_meta rows:", cursor.fetchone()[0])

cursor.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE '%mosquitto%' OR entity_id LIKE '%boiler%' OR entity_id LIKE '%temp%'")
rows = cursor.fetchall()
print("Matching entity IDs:")
for r in rows:
    print(r[0])
conn.close()
