import json
import os

entries_path = r"\\192.168.100.5\config\.storage\core.config_entries"
if os.path.exists(entries_path):
    with open(entries_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    entries = data.get("data", {}).get("entries", [])
    for entry in entries:
        if entry.get("domain") == "template":
            # Check if this template entry defines boiler_target_temp
            options = entry.get("options", {})
            template_code = options.get("state", "")
            name = entry.get("title", "")
            # Let's check unique ID or title
            if "boiler_target_temp" in str(entry) or "boiler_target_temp" in template_code:
                print("=== Template Entry ===")
                print("Title:", name)
                print("Options:", json.dumps(options, indent=2))
else:
    print("core.config_entries not found.")
