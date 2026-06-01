import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Matches for elec_pump in ems.log ===")
    matches = [line for line in lines if "elec_pump" in line.lower()]
    print("Total matches:", len(matches))
    for m in matches[-100:]:
        print(m, end="")
else:
    print("Log not found.")
