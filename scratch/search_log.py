import os
import sys
import re

sys.stdout.reconfigure(encoding='utf-8')

debate_path = "DEBATE.md"
if os.path.exists(debate_path):
    with open(debate_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    # Search for "измен" (case-insensitive)
    matches = [m.start() for m in re.finditer("измен", content, re.IGNORECASE)]
    print("Found 'измен' occurrences:", len(matches))
    for m in matches:
        start = max(0, m - 100)
        end = min(len(content), m + 150)
        snippet = content[start:end].replace('\n', ' ')
        print(f"Index {m}: ... {snippet} ...")
else:
    print("DEBATE.md not found.")
