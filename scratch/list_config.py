import os

config_dir = r"\\192.168.100.5\config"
if os.path.exists(config_dir):
    for f in sorted(os.listdir(config_dir)):
        path = os.path.join(config_dir, f)
        if os.path.isfile(path):
            print(f, os.path.getsize(path), "bytes")
        else:
            print(f + "/", "[DIR]")
else:
    print("Config dir does not exist")
