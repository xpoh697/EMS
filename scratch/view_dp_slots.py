import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    # We want to print lines containing "Using total average load" or "Parsed Buy Prices" or "PV forecast update"
    # or details of DP run around 14:00
    for line in lines[-2000:]:
        if any(keyword in line for keyword in ("Parsed Buy", "Parsed Sell", "PV Forecast", "Using total average", "EMS DP:", "run_unified_dp")):
            if "boiler" not in line.lower() or "EMS DP:" in line:
                print(line, end="")
else:
    print("Log not found.")
