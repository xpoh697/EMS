import os

config_dir = r"\\192.168.100.5\config"
matches = []
if os.path.exists(config_dir):
    for root, dirs, files in os.walk(config_dir):
        for file in files:
            if file.endswith(".yaml"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if "boiler_target_temp" in content:
                        matches.append(path)
                except Exception as e:
                    pass

print("Files containing boiler_target_temp:")
for path in matches:
    print(f"  {path}")
