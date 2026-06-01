import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Mode-setting logs on May 24 ===")
    for line in lines:
        if "2026-05-24" in line and ("Setting bypass valve" in line or "Setting circulation pump" in line or "Setting electric heater" in line or "Setting gas climate" in line):
            print(line, end="")
else:
    print("Log not found.")
