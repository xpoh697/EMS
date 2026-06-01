import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "pv_today" in line.lower() or "consumption_today" in line.lower() or "scaled_slots" in line.lower() or "pv_kwh" in line.lower():
                print(line, end="")
else:
    print("Log not found.")
