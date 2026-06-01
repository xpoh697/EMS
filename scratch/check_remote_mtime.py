import os
import time

remote_file = r"\\192.168.100.5\config\custom_components\ems\boiler_controller.py"
local_file = r"custom_components/ems/boiler_controller.py"

if os.path.exists(remote_file):
    print("Remote mtime:", time.ctime(os.path.getmtime(remote_file)))
else:
    print("Remote file not found.")

if os.path.exists(local_file):
    print("Local mtime:", time.ctime(os.path.getmtime(local_file)))
else:
    print("Local file not found.")
