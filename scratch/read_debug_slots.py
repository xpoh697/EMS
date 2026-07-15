import json
import os

path = r'\\192.168.100.5\config\ems_debug_slots.json'
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print("Timestamp:", data.get("timestamp"))
    print("Boiler DP State:", data.get("boiler_dp_state"))
    
    slots = data.get("slots_passed", [])
    print(f"Total slots: {len(slots)}")
    for s in slots:
        # Check today's slots
        if s.get("date") == "2026-07-08":
            print(f"H{s.get('hour')}: Mode={s.get('physical_mode')}, Action={s.get('action')}, Buy={s.get('buy_price')}, Sell={s.get('sell_price')}, PV={s.get('pv_kwh')}, Cons={s.get('consumption_kwh')}, SOC={s.get('expected_soc')}, Energy={s.get('energy_kwh')}")
else:
    print("ems_debug_slots.json not found")
