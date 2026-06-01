import os
import sys

# Set standard output encoding to utf-8
sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== All boiler_controller logs ===")
    count = 0
    # Let's print the last 200 matches
    matched_lines = []
    for line in lines:
        if "boiler_controller" in line:
            matched_lines.append(line)
    
    for line in matched_lines[-200:]:
        print(line, end="")
else:
    print("Log file not found.")
