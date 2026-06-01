import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Electric boiler temperatures on May 24 ===")
    count = 0
    for line in lines:
        if "2026-05-24" in line and ("temp" in line.lower() or "temperature" in line.lower()):
            if "electric boiler temperature" in line.lower() or "elec" in line.lower() or "water" in line.lower():
                print(line, end="")
                count += 1
                if count > 100:
                    break
else:
    print("Log not found.")
