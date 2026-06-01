import os

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # We want to find the lines after "EMS DP: Using total average load profile tomorrow"
    # because that's when the actual DP runs and logs the slot details.
    found_runs = []
    current_run = []
    is_recording = False
    
    for line in lines:
        if "EMS DP:" in line:
            if "Using total average load profile tomorrow" in line:
                is_recording = True
                current_run = [line]
            elif is_recording:
                current_run.append(line)
        elif is_recording and not line.startswith("2026-06-01"):
            # Check if it's continued multiline or from the same thread
            current_run.append(line)
        else:
            if is_recording:
                found_runs.append(current_run)
                is_recording = False
                
    if is_recording:
        found_runs.append(current_run)
        
    print(f"Found {len(found_runs)} DP runs in logs. Printing the last one:")
    if found_runs:
        for line in found_runs[-1]:
            print(line, end="")
else:
    print("Log file not found.")
