import sqlite3

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT metadata_id, entity_id FROM states_meta WHERE entity_id LIKE '%ems%' OR entity_id LIKE '%dp%' OR entity_id LIKE '%scheduler%'")
rows = cursor.fetchall()
print("Matching entity IDs:")
for r in rows:
    print(f"ID {r[0]}: {r[1]}")

conn.close()
