import sqlite3
conn = sqlite3.connect(r"\\192.168.100.5\config\home-assistant_v2.db")
print("EMS entities:")
for r in conn.execute("SELECT entity_id FROM states_meta WHERE entity_id LIKE '%ems%' OR entity_id LIKE '%scheduler%'"):
    print(r[0])
