import sqlite3
conn = sqlite3.connect(r"\\192.168.100.5\config\home-assistant_v2.db")
print("All entities in database:")
for r in conn.execute("SELECT entity_id FROM states_meta ORDER BY entity_id"):
    print(r[0])
