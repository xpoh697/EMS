import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

auto_path = r"\\192.168.100.5\config\automations.yaml"
if os.path.exists(auto_path):
    with open(auto_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    lines = content.splitlines()
    print("=== Match at line 1215 ===")
    start = max(0, 1215 - 20)
    end = min(len(lines), 1215 + 30)
    for i in range(start, end):
        print(f"{i+1}: {lines[i]}")
else:
    print("automations.yaml not found.")
