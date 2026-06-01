import os
import re

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Let's search for "schedule" in the logs
    # We want to find log messages from custom_components.ems.sensor or dp_engine
    # containing schedule or trajectory details
    lines = content.splitlines()
    print("Total lines in ems.log:", len(lines))
    
    # Find lines containing "trajectory" or "schedule"
    matching_lines = []
    for idx, line in enumerate(lines):
        if "trajectory" in line.lower() or "schedule" in line.lower() or "run_unified_dp" in line.lower():
            matching_lines.append((idx, line))
            
    print(f"Found {len(matching_lines)} matching lines. Showing last 30:")
    for idx, line in matching_lines[-30:]:
        print(f"Line {idx}: {line}")
else:
    print("Log file not found.")
