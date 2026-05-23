import sqlite3

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE '%boiler%' OR entity_id LIKE '%ems%' OR entity_id LIKE '%dp%'")
res = cursor.fetchall()
print("Entity count:", len(res))
for r in sorted(res):
    print(r[0])

conn.close()
