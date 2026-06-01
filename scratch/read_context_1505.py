import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"\\192.168.100.5\config\ems.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    
    print("=== Context around 15:05 ===")
    for idx, line in enumerate(lines):
        if "2026-05-24 15:04:06" in line or "2026-05-24 15:04:10" in line:
            # Print from 20 lines before to 20 lines after
            start = max(0, idx - 25)
            end = min(len(lines), idx + 25)
            for i in range(start, end):
                print(f"{i}: {lines[i]}", end="")
            break
else:
    print("Log not found.")
