import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')
db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    SELECT entity_id 
    FROM states_meta 
    WHERE entity_id LIKE '%temp%' 
       OR entity_id LIKE '%water%' 
       OR entity_id LIKE '%climate%' 
       OR entity_id LIKE '%control%'
       OR entity_id LIKE '%dp%'
""")
rows = cursor.fetchall()
print("Found entities:")
for row in rows:
    print(f"  {row[0]}")
