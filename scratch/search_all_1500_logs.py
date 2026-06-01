import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== All logs 15:00 - 15:10 ===")
    for line in lines:
        if "2026-05-24 15:0" in line:
            print(line, end="")
else:
    print("Log not found.")
