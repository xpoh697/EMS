import os

file_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\sensor.py"

with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update extra_state_attributes
target1 = """    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        \"\"\"Return the state attributes.\"\"\"
        return {
            "schedule": self._schedule,
            "stats": self._stats,
            "recommended_bypass": self._recommended_bypass,
            "t_start": self._t_start,
            "t_gas": self._t_gas,
            "t_elec": self._t_elec,
            "t_min": self._t_min,
            "t_max_elec": self._t_max_elec,
            "t_max_gas": self._t_max_gas,
            "vol_elec": self._vol_elec,
            "vol_gas": self._vol_gas,
            "gas_cost_m3": self._gas_cost_m3,
            "last_calculation": self._last_calc_time.isoformat() if self._last_calc_time else None,
            "calculation_duration": self._calc_duration,
        }"""

replacement1 = """    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        \"\"\"Return the state attributes.\"\"\"
        controller = self.hass.data[DOMAIN][self._entry_id].get("boiler_controller")
        manual_active = False
        manual_mode = None
        manual_setpoint = None
        if controller:
            manual_active = getattr(controller, "_manual_heating_active", False)
            manual_mode = getattr(controller, "_manual_heating_mode", None)
            manual_setpoint = getattr(controller, "_manual_heating_setpoint", None)

        return {
            "schedule": self._schedule,
            "stats": self._stats,
            "recommended_bypass": self._recommended_bypass,
            "t_start": self._t_start,
            "t_gas": self._t_gas,
            "t_elec": self._t_elec,
            "t_min": self._t_min,
            "t_max_elec": self._t_max_elec,
            "t_max_gas": self._t_max_gas,
            "vol_elec": self._vol_elec,
            "vol_gas": self._vol_gas,
            "gas_cost_m3": self._gas_cost_m3,
            "last_calculation": self._last_calc_time.isoformat() if self._last_calc_time else None,
            "calculation_duration": self._calc_duration,
            "manual_heating_active": manual_active,
            "manual_heating_mode": manual_mode,
            "manual_heating_setpoint": manual_setpoint,
        }"""

if target1 not in code:
    raise ValueError("Target 1 not found in code")
code = code.replace(target1, replacement1, 1)

# 2. Add manual heating event listener
target2 = """        # Recalculate on every hour transition
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )"""

replacement2 = """        # Recalculate on every hour transition
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_hourly_trigger, minute=0, second=0
            )
        )

        # Listen to manual heating cycle updates
        async def handle_manual_heating_update(event):
            self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen("ems_manual_heating_updated", handle_manual_heating_update)
        )"""

if target2 not in code:
    raise ValueError("Target 2 not found in code")
code = code.replace(target2, replacement2, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("sensor.py modified successfully.")
