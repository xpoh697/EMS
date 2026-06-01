import os

storage_dir = r"\\192.168.100.5\config\.storage"
matches = []
if os.path.exists(storage_dir):
    for root, dirs, files in os.walk(storage_dir):
        for file in files:
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if "boiler_target_temp" in content:
                    matches.append(path)
            except Exception as e:
                pass

print("Storage files containing boiler_target_temp:")
for path in matches:
    print(f"  {path}")
