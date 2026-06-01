import os

config_dir = r"\\192.168.100.5\config"
print("Config dir exists:", os.path.exists(config_dir))

matches = []
if os.path.exists(config_dir):
    for root, dirs, files in os.walk(config_dir):
        for file in files:
            if file.endswith(".yaml"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if "mosquitto_broker_diyless_dhw" in content:
                        matches.append((path, "mosquitto_broker_diyless_dhw"))
                    if "set_temperature" in content:
                        matches.append((path, "set_temperature"))
                except Exception as e:
                    pass

print("Matches found in config files:")
for path, term in matches:
    print(f"  {path} -> {term}")
