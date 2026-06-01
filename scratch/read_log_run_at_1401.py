import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    start_printing = False
    count = 0
    for line in lines:
        if "2026-06-01 14:01:12.559" in line:
            start_printing = True
        if start_printing:
            print(line, end="")
            count += 1
            if count > 150:
                break
else:
    print("Log not found.")
