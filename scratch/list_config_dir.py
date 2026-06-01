import os

config_dir = r"\\192.168.100.5\config"
if os.path.exists(config_dir):
    files = sorted(os.listdir(config_dir))
    print("Files in HA config directory:")
    for f in files:
        path = os.path.join(config_dir, f)
        if os.path.isfile(path):
            print(f"  {f} ({os.path.getsize(path)} bytes)")
        else:
            print(f"  {f}/ [DIR]")
else:
    print("HA config directory not found.")
