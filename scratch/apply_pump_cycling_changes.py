import os

controller_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\boiler_controller.py"

with open(controller_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update override check
target_override = """                    elif entity_id == self.pump:
                        if self._elec_pump_dump_active and "ELEC" in mode:
                            expected_state = STATE_ON
                        else:
                            expected_state = STATE_ON if ("PUMP" in mode and recommended_bypass == "ON") else STATE_OFF"""

replacement_override = """                    elif entity_id == self.pump:
                        if "ELEC" in mode:
                            expected_state = STATE_ON if self._elec_pump_dump_active else STATE_OFF
                        else:
                            expected_state = STATE_ON if ("PUMP" in mode and recommended_bypass == "ON") else STATE_OFF"""

# 2. Update set_boiler_mode actuator logic
target_actuator = """                # Принудительно включаем циркуляцию при аварийном сбросе тепла ТЭНа
                if self._elec_pump_dump_active and "ELEC" in mode:
                    target_pump_state_logical = True
                else:
                    target_pump_state_logical = "PUMP" in mode"""

replacement_actuator = """                if "ELEC" in mode:
                    target_pump_state_logical = self._elec_pump_dump_active
                else:
                    target_pump_state_logical = "PUMP" in mode"""

if target_override in content:
    content = content.replace(target_override, replacement_override, 1)
    print("Override check logic replaced successfully.")
else:
    print("Warning: target_override not found in boiler_controller.py")

if target_actuator in content:
    content = content.replace(target_actuator, replacement_actuator, 1)
    print("Actuator pump logic replaced successfully.")
else:
    print("Warning: target_actuator not found in boiler_controller.py")

with open(controller_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Apply script finished.")
