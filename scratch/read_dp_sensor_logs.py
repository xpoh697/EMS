import os
log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "EMS DP:" in line:
                print(line, end="")
