import sqlite3

db_path = "\\\\192.168.100.5\\config\\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE '%scheduler%'")
for r in sorted(cursor.fetchall()):
    print(r[0])
conn.close()
