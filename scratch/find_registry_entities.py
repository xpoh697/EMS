import json
import os

reg_path = r"\\192.168.100.5\config\.storage\core.entity_registry"
if os.path.exists(reg_path):
    with open(reg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("EMS entities in registry:")
    entities = data.get("data", {}).get("entities", [])
    for ent in entities:
        if ent.get("platform") == "ems":
            print(f"Entity ID: {ent.get('entity_id')} | Config Entry: {ent.get('config_entry_id')}")
else:
    print("Registry not found.")
