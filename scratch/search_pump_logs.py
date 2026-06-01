import os

log_path = r"\\192.168.100.5\config\ems.log"

if os.path.exists(log_path):
    print("Reading ems.log...")
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    valve_lines = [l for l in lines if "bypass valve" in l.lower() or "setting bypass" in l.lower()]
    print(f"Total bypass valve setting lines: {len(valve_lines)}")
    print("=== Last 40 bypass valve setting log lines ===")
    for l in valve_lines[-40:]:
        print(l, end="")
else:
    print("ems.log does not exist.")
