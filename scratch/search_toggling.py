import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Filtered lines around toggling ===")
    for line in lines:
        if "13:4" in line or "13:5" in line:
            if any(term in line for term in ["EMS Boiler DP", "Boiler Controller", "sensor.boiler", "sensor.1st_power"]):
                print(line, end="")
else:
    print("Log not found.")
