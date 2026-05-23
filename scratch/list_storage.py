import os

storage_dir = r"\\192.168.100.5\config\.storage"
if os.path.exists(storage_dir):
    for f in sorted(os.listdir(storage_dir)):
        path = os.path.join(storage_dir, f)
        if os.path.isfile(path):
            print(f, os.path.getsize(path), "bytes")
        else:
            print(f + "/", "[DIR]")
else:
    print("Storage dir does not exist")
