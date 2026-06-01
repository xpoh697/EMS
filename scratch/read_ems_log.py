import os

log_path = r"\\192.168.100.5\config\ems.log"
print("Log exists:", os.path.exists(log_path))
if os.path.exists(log_path):
    print("Log size:", os.path.getsize(log_path))
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    print("Last 50 lines of ems.log:")
    for line in lines[-50:]:
        print(line, end="")
