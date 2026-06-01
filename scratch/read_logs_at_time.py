import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    print("Lines in ems.log around 14:01:")
    for line in lines:
        if "2026-06-01 14:01" in line or "2026-06-01 14:00" in line:
            print(line, end="")
else:
    print("Log not found.")
