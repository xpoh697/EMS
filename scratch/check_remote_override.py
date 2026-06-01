import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

remote_file = r"\\192.168.100.5\config\custom_components\ems\boiler_controller.py"
if os.path.exists(remote_file):
    with open(remote_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    idx = content.find("async def _async_override_check")
    if idx != -1:
        print("Found _async_override_check on remote:")
        print(content[idx:idx+2000])
    else:
        print("_async_override_check NOT found on remote.")
else:
    print("Remote file not found.")
