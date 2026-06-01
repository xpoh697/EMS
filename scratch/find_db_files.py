import os
config_dir = r"\\192.168.100.5\config"
if os.path.exists(config_dir):
    print("Found files:")
    for f in os.listdir(config_dir):
        if f.endswith(".db") or "home-assistant" in f:
            print(f"  {f} - Size: {os.path.getsize(os.path.join(config_dir, f))}")
else:
    print("Config dir not found.")
