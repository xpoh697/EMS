import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Sensor logs 14:50 - 15:15 ===")
    for line in lines:
        if "2026-05-24 14:" in line or "2026-05-24 15:" in line:
            if "sensor" in line.lower() or "run_boiler_dp" in line.lower():
                print(line, end="")
else:
    print("Log not found.")
