import os

file_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\boiler_controller.py"

with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update Constructor
target1 = """        # Флаги программной отсечки нагрева и перекачки тепла
        self._elec_cutoff_active = False
        self._gas_cutoff_active = False
        self._elec_pump_dump_active = False"""

replacement1 = """        # Флаги программной отсечки нагрева и перекачки тепла
        self._elec_cutoff_active = False
        self._gas_cutoff_active = False
        self._elec_pump_dump_active = False
        
        # Флаги ручного цикла нагрева
        self._manual_heating_active = False
        self._manual_heating_mode = "GAS"
        self._manual_heating_setpoint = 50.0
        self._manual_pump_dump_active = False"""

if target1 not in code:
    raise ValueError("Target 1 not found in code")
code = code.replace(target1, replacement1, 1)

# 2. Update System Mode Changed
target2 = """    async def _async_system_mode_changed(self, event):
        \"\"\"Handle system mode selection changes (Auto/Manual).\"\"\"
        new_state = event.data.get("new_state")
        if new_state:
            self.current_mode = new_state.state
            await self._async_apply_current_dp_plan()"""

replacement2 = """    async def _async_system_mode_changed(self, event):
        \"\"\"Handle system mode selection changes (Auto/Manual).\"\"\"
        new_state = event.data.get("new_state")
        if new_state:
            self.current_mode = new_state.state
            if self.current_mode.lower() != "manual":
                await self.async_stop_manual_heating()
            await self._async_apply_current_dp_plan()"""

if target2 not in code:
    raise ValueError("Target 2 not found in code")
code = code.replace(target2, replacement2, 1)

# 3. Update Temp Changed and Add Manual Heating Methods
target3 = """    async def _async_temp_changed(self, event):
        \"\"\"Обработка изменения температуры бойлеров.\"\"\"
        if self.current_mode.lower() == "auto":
            await self._async_apply_current_dp_plan()"""

replacement3 = """    async def _async_temp_changed(self, event):
        \"\"\"Обработка изменения температуры бойлеров.\"\"\"
        if self.current_mode.lower() == "auto":
            await self._async_apply_current_dp_plan()
        elif self.current_mode.lower() == "manual" and self._manual_heating_active:
            await self._async_apply_manual_heating()

    async def async_start_manual_heating(self, mode: str, setpoint: float):
        \"\"\"Запуск ручного цикла нагрева с жесткой валидацией на бэкенде.\"\"\"
        if self.current_mode.lower() != "manual":
            raise HomeAssistantError("Cannot start manual heating: system mode is not Manual.")
            
        valid_modes = ["GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP"]
        if mode not in valid_modes:
            raise HomeAssistantError(f"Invalid manual heating mode: {mode}")

        # Валидация сетпоинта: минимум t_min, максимум по настроенным максимумам котлов
        t_min = float(self.config.get("thermostat_set_temp", 45.0))
        if "GAS" in mode:
            t_max = float(self.config.get("gas_boiler_max_temp", 50.0))
        else:
            t_max = float(self.config.get("elec_boiler_max_temp", 70.0))

        val_setpoint = float(setpoint)
        if val_setpoint < t_min or val_setpoint > t_max:
            raise HomeAssistantError(
                f"Setpoint {val_setpoint:.1f}°C is out of bounds [{t_min:.1f} - {t_max:.1f}] for mode {mode}."
            )

        self._manual_heating_active = True
        self._manual_heating_mode = mode
        self._manual_heating_setpoint = val_setpoint
        self._manual_pump_dump_active = False

        _LOGGER.info("EMS Boiler Controller: Starting manual heating cycle. Mode: %s, Setpoint: %.1f°C", mode, val_setpoint)
        
        # Trigger immediate actuation
        await self._async_apply_manual_heating()
        self.hass.bus.async_fire("ems_manual_heating_updated")

    async def async_stop_manual_heating(self):
        \"\"\"Остановка ручного цикла нагрева и принудительное отключение нагревателей.\"\"\"
        if not self._manual_heating_active:
            return
            
        self._manual_heating_active = False
        _LOGGER.info("EMS Boiler Controller: Stopping manual heating cycle.")
        
        self._is_applying_dp_plan = True
        try:
            if self.elec_heater:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN,
                    SERVICE_TURN_OFF,
                    {ATTR_ENTITY_ID: self.elec_heater}
                )
            if self.gas_climate:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    "set_hvac_mode",
                    {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": "off"}
                )
            if self.pump:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN,
                    SERVICE_TURN_OFF,
                    {ATTR_ENTITY_ID: self.pump}
                )
        except Exception as ex:
            _LOGGER.error("Error shutting down manual heating devices: %s", ex)
        finally:
            self._is_applying_dp_plan = False
            
        self.hass.bus.async_fire("ems_manual_heating_updated")

    async def _async_apply_manual_heating(self):
        \"\"\"Выполнение ручного цикла нагрева в зависимости от выбранного режима.\"\"\"
        if not self._manual_heating_active:
            return

        if self.current_mode.lower() != "manual":
            self._manual_heating_active = False
            return

        mode = self._manual_heating_mode
        setpoint = self._manual_heating_setpoint

        # 1. Проверяем достижение целевой температуры
        if "GAS" in mode:
            temp = self._get_gas_temp()
        else:
            temp = self._get_elec_temp()

        if temp is not None and temp >= setpoint:
            _LOGGER.info("EMS Boiler Controller: Manual heating cycle complete (%.1f >= %.1f). Stopping.", temp, setpoint)
            await self.async_stop_manual_heating()
            return

        # Fail-Safe при потере связи
        if temp is None:
            _LOGGER.warning("EMS Boiler Controller: Temperature sensor is unavailable during manual heating. Safety shutdown.")
            await self.async_stop_manual_heating()
            return

        self._is_applying_dp_plan = True
        try:
            # 2. Управление байпасом: GAS -> OFF (закрыт), остальные -> ON (открыт)
            target_bypass = "OFF" if mode == "GAS" else "ON"
            if self.bypass_valve:
                valve_domain = self.bypass_valve.split(".")[0]
                current_valve = self.hass.states.get(self.bypass_valve)
                target_service = SERVICE_TURN_ON if target_bypass == "ON" else SERVICE_TURN_OFF
                target_state = STATE_ON if target_bypass == "ON" else STATE_OFF
                if not current_valve or current_valve.state != target_state:
                    _LOGGER.info("EMS Boiler Controller (Manual): Setting bypass valve from %s to %s", current_valve.state if current_valve else "unknown", target_state)
                    await self.hass.services.async_call(
                        valve_domain,
                        target_service,
                        {ATTR_ENTITY_ID: self.bypass_valve}
                    )

            # 3. Управление ТЭНом: ELEC/ELEC_PUMP -> ON, остальные -> OFF
            target_heater_state = STATE_ON if "ELEC" in mode else STATE_OFF
            target_heater_service = SERVICE_TURN_ON if target_heater_state == STATE_ON else SERVICE_TURN_OFF
            if self.elec_heater:
                current_heater = self.hass.states.get(self.elec_heater)
                if not current_heater or current_heater.state != target_heater_state:
                    _LOGGER.info("EMS Boiler Controller (Manual): Setting electric heater switch from %s to %s", current_heater.state if current_heater else "unknown", target_heater_state)
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_heater_service,
                        {ATTR_ENTITY_ID: self.elec_heater}
                    )

            # 4. Управление газом: GAS/GAS_PUMP -> heat, остальные -> off
            target_hvac = "heat" if "GAS" in mode else "off"
            if self.gas_climate:
                current_gas = self.hass.states.get(self.gas_climate)
                if not current_gas or current_gas.state != target_hvac:
                    _LOGGER.info("EMS Boiler Controller (Manual): Setting gas climate HVAC mode from %s to %s", current_gas.state if current_gas else "unknown", target_hvac)
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        "set_hvac_mode",
                        {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": target_hvac}
                    )
                if target_hvac == "heat":
                    current_target_temp = current_gas.attributes.get("temperature") if current_gas else None
                    if current_target_temp != setpoint:
                        await self.hass.services.async_call(
                            CLIMATE_DOMAIN,
                            "set_temperature",
                            {ATTR_ENTITY_ID: self.gas_climate, ATTR_TEMPERATURE: setpoint}
                        )

            # 5. Управление насосом перекачки:
            # - GAS -> OFF
            # - GAS_PUMP -> ON (постоянно)
            # - ELEC -> OFF
            # - ELEC_PUMP -> периодическая перекачка по порогам setpoint - 5 и setpoint - 10
            if self.pump:
                if mode == "GAS":
                    target_pump_state_logical = False
                elif mode == "GAS_PUMP":
                    target_pump_state_logical = True
                elif mode == "ELEC":
                    target_pump_state_logical = False
                elif mode == "ELEC_PUMP":
                    t_pump_on = setpoint - 5.0
                    t_pump_off = setpoint - 10.0
                    if temp >= t_pump_on:
                        self._manual_pump_dump_active = True
                    elif temp < t_pump_off:
                        self._manual_pump_dump_active = False
                    target_pump_state_logical = self._manual_pump_dump_active
                else:
                    target_pump_state_logical = False

                target_pump_service = SERVICE_TURN_ON if target_pump_state_logical else SERVICE_TURN_OFF
                target_pump_state = STATE_ON if target_pump_state_logical else STATE_OFF

                # Safety check: bypass must be open to run pump
                if target_pump_service == SERVICE_TURN_ON and target_bypass != "ON":
                    _LOGGER.warning("EMS Boiler Controller (Manual): Prevented turning pump ON because bypass is closed")
                    target_pump_service = SERVICE_TURN_OFF
                    target_pump_state = STATE_OFF
                
                current_pump = self.hass.states.get(self.pump)
                if not current_pump or current_pump.state != target_pump_state:
                    _LOGGER.info("EMS Boiler Controller (Manual): Setting circulation pump from %s to %s", current_pump.state if current_pump else "unknown", target_pump_state)
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_pump_service,
                        {ATTR_ENTITY_ID: self.pump}
                    )
        except Exception as ex:
            _LOGGER.error("Error applying manual heating: %s", ex)
        finally:
            self._is_applying_dp_plan = False"""

if target3 not in code:
    raise ValueError("Target 3 not found in code")
code = code.replace(target3, replacement3, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("boiler_controller.py modified successfully.")
