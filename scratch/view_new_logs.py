import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    print("=== Logs between 23:10 and 23:22 ===")
    for line in lines:
        if "2026-05-23 23:1" in line or "2026-05-23 23:20" in line or "2026-05-23 23:21" in line:
            print(line, end="")
else:
    print("Log file not found.")
