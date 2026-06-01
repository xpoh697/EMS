import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

debate_path = "e:/HA_INTEGRATIONS/EMS/DEBATE.md"
if os.path.exists(debate_path):
    with open(debate_path, "r", encoding="utf-8") as f:
        # Seek near the end
        f.seek(0, 2)
        size = f.tell()
        # Read last 5000 bytes
        f.seek(max(0, size - 5000))
        tail = f.read()
    print("Tail of DEBATE.md:")
    print(tail)
else:
    print("DEBATE.md not found.")
