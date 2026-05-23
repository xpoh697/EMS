import os

db_path = r"\\192.168.100.5\config\home-assistant_v2.db"
dir_path = os.path.dirname(db_path)
for f in os.listdir(dir_path):
    if f.startswith("home-assistant_v2.db"):
        path = os.path.join(dir_path, f)
        print(f, os.path.getsize(path), "bytes")
