import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

auto_path = r"\\192.168.100.5\config\automations.yaml"
if os.path.exists(auto_path):
    with open(auto_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    lines = content.splitlines()
    print("=== Matches for boiler_loading ===")
    for idx, line in enumerate(lines):
        if "boiler_loading" in line:
            print(f"Line {idx+1}: {line}")
            start = max(0, idx - 5)
            end = min(len(lines), idx + 15)
            for i in range(start, end):
                print(f"  {i+1}: {lines[i]}")
else:
    print("automations.yaml not found.")
