import json
import glob
import os

config_entries_path = r"\\192.168.100.5\config\.storage\core.config_entries"

if os.path.exists(config_entries_path):
    with open(config_entries_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    entries = data.get("data", {}).get("entries", [])
    ems_entries = [e for e in entries if e.get("domain") == "ems"]
    
    if ems_entries:
        for idx, entry in enumerate(ems_entries):
            print(f"=== EMS Entry {idx} ===")
            print("Title:", entry.get("title"))
            print("Entry ID:", entry.get("entry_id"))
            print("Data:", json.dumps(entry.get("data"), indent=2))
            print("Options:", json.dumps(entry.get("options"), indent=2))
            
            # Check if there is a calibration file for this entry
            cal_file = rf"\\192.168.100.5\config\.storage\ems_calibration_{entry.get('entry_id')}"
            if os.path.exists(cal_file):
                with open(cal_file, "r", encoding="utf-8") as cf:
                    cal_data = json.load(cf)
                print("Calibration Data:", json.dumps(cal_data, indent=2))
            else:
                print("No calibration file found.")
    else:
        print("No EMS config entries found in core.config_entries.")
else:
    print("core.config_entries not found.")
