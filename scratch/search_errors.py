import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    print("=== Matches for warning/error/cutoff ===")
    for line in lines[-200:]:
        line_lower = line.lower()
        if "cutoff" in line_lower or "warning" in line_lower or "error" in line_lower or "unavailable" in line_lower:
            print(line, end="")
else:
    print("Log file not found.")
