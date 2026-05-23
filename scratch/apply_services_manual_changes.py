import os

services_py_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\services.py"
services_yaml_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\services.yaml"

# 1. Modify services.py
with open(services_py_path, "r", encoding="utf-8") as f:
    py_code = f.read()

target1 = """START_CALIBRATION_SCHEMA = vol.Schema({
    vol.Required("phase"): vol.In(["gas_only", "gas_with_pump", "elec_only", "elec_with_pump"]),
    vol.Optional("heating_duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("target_temperature_delta"): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=50.0)),
    vol.Optional("stabilization_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
})"""

replacement1 = """START_CALIBRATION_SCHEMA = vol.Schema({
    vol.Required("phase"): vol.In(["gas_only", "gas_with_pump", "elec_only", "elec_with_pump"]),
    vol.Optional("heating_duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("target_temperature_delta"): vol.All(vol.Coerce(float), vol.Range(min=1.0, max=50.0)),
    vol.Optional("stabilization_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
})

START_MANUAL_HEATING_SCHEMA = vol.Schema({
    vol.Required("mode"): vol.In(["GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]),
    vol.Required("setpoint"): vol.All(vol.Coerce(float), vol.Range(min=20.0, max=85.0)),
})"""

if target1 not in py_code:
    raise ValueError("Target 1 not found in services.py")
py_code = py_code.replace(target1, replacement1, 1)

target2 = """    hass.services.async_register(
        DOMAIN, SERVICE_START_CALIBRATION, handle_start_calibration, schema=START_CALIBRATION_SCHEMA
    )
    _LOGGER.debug("EMS services successfully registered")"""

replacement2 = """    async def handle_start_manual_heating(call: ServiceCall) -> None:
        \"\"\"Handle starting manual heating.\"\"\"
        mode = call.data["mode"]
        setpoint = call.data["setpoint"]
        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_start_manual_heating(mode, setpoint)

    async def handle_stop_manual_heating(call: ServiceCall) -> None:
        \"\"\"Handle stopping manual heating.\"\"\"
        controller = hass.data[DOMAIN][entry.entry_id].get("boiler_controller")
        if not controller:
            _LOGGER.error("Boiler controller not initialized for config entry %s", entry.entry_id)
            return
        await controller.async_stop_manual_heating()

    hass.services.async_register(
        DOMAIN, SERVICE_START_CALIBRATION, handle_start_calibration, schema=START_CALIBRATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "start_manual_heating", handle_start_manual_heating, schema=START_MANUAL_HEATING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "stop_manual_heating", handle_stop_manual_heating
    )
    _LOGGER.debug("EMS services successfully registered")"""

if target2 not in py_code:
    raise ValueError("Target 2 not found in services.py")
py_code = py_code.replace(target2, replacement2, 1)

target3 = """def async_unload_services(hass: HomeAssistant) -> None:
    \"\"\"Unload EMS services.\"\"\"
    for service in [
        SERVICE_SET_OVERRIDE,
        SERVICE_CLEAR_OVERRIDE,
        SERVICE_CLEAR_ALL_OVERRIDES,
        SERVICE_START_CALIBRATION,
    ]:"""

replacement3 = """def async_unload_services(hass: HomeAssistant) -> None:
    \"\"\"Unload EMS services.\"\"\"
    for service in [
        SERVICE_SET_OVERRIDE,
        SERVICE_CLEAR_OVERRIDE,
        SERVICE_CLEAR_ALL_OVERRIDES,
        SERVICE_START_CALIBRATION,
        "start_manual_heating",
        "stop_manual_heating",
    ]:"""

if target3 not in py_code:
    raise ValueError("Target 3 not found in services.py")
py_code = py_code.replace(target3, replacement3, 1)

with open(services_py_path, "w", encoding="utf-8") as f:
    f.write(py_code)

print("services.py modified successfully.")

# 2. Modify services.yaml
with open(services_yaml_path, "r", encoding="utf-8") as f:
    yaml_code = f.read()

yaml_append = """
start_manual_heating:
  name: Start Manual Heating
  description: Starts a manual heating cycle with the selected mode and target temperature setpoint.
  fields:
    mode:
      name: Heating Mode
      description: The heating mode to run.
      required: true
      example: ELEC
      selector:
        select:
          options:
            - label: GAS - gas only
              value: GAS
            - label: GAS_PUMP - Gas + pump
              value: GAS_PUMP
            - label: ELEC - Electro only
              value: ELEC
            - label: Elec_pump - eclectro + pump
              value: ELEC_PUMP
    setpoint:
      name: Target Temperature Setpoint
      description: Target temperature to heat the boiler up to.
      required: true
      example: 65.0
      selector:
        number:
          min: 20.0
          max: 85.0
          step: 0.5
          mode: box

stop_manual_heating:
  name: Stop Manual Heating
  description: Stops the active manual heating cycle and shuts down heating elements.
"""

new_yaml_code = yaml_code.strip() + "\n" + yaml_append

with open(services_yaml_path, "w", encoding="utf-8") as f:
    f.write(new_yaml_code)

print("services.yaml modified successfully.")
