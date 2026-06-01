import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    print("DP execution logs in ems.log:")
    for line in lines:
        if "using total average load profile" in line.lower() or "async_update_strategy" in line.lower() or "error" in line.lower():
            if "sensor" in line:
                print(line, end="")
else:
    print("Log not found.")
