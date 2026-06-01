import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

auto_path = r"\\192.168.100.5\config\automations.yaml"
if os.path.exists(auto_path):
    with open(auto_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    # Let's search for "sensor.scheduler" in the content
    lines = content.splitlines()
    matches = []
    for idx, line in enumerate(lines):
        if "sensor.scheduler" in line:
            matches.append((idx, line))
            
    print(f"Found {len(matches)} matches:")
    for idx, line in matches:
        print(f"Line {idx+1}: {line}")
        # Print surrounding lines
        print("  Context:")
        start = max(0, idx - 10)
        end = min(len(lines), idx + 20)
        for i in range(start, end):
            print(f"    {i+1}: {lines[i]}")
        print("-" * 50)
else:
    print("automations.yaml not found.")
