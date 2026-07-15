import os
import sys

# We can run a script that inspects Home Assistant's config entries.
# Since we are running in the workspace, we don't have direct access to `hass` here,
# but we can look at the config entry file inside HA's config directory:
# \\192.168.100.5\config\.storage\core.config_entries

storage_path = r"\\192.168.100.5\config\.storage\core.config_entries"

if os.path.exists(storage_path):
    print("Found core.config_entries!")
    with open(storage_path, "r", encoding="utf-8") as f:
        data = json = json_data = f.read()
    
    import json
    entries = json.loads(data)
    for entry in entries.get("data", {}).get("entries", []):
        if entry.get("domain") == "ems":
            print("EMS Entry:")
            print(json.dumps(entry, indent=2))
else:
    print("Could not find core.config_entries at standard storage path.")
