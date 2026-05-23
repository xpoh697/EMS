import os

file_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\boiler_controller.py"

with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Constructor addition
target1 = """        # Mode property, will be updated by EmsBoilerModeSelect
        self.current_mode = "Auto"
        
        # Capacity and cost settings"""

replacement1 = """        # Mode property, will be updated by EmsBoilerModeSelect
        self.current_mode = "Auto"
        
        # Флаги программной отсечки нагрева и перекачки тепла
        self._elec_cutoff_active = False
        self._gas_cutoff_active = False
        self._elec_pump_dump_active = False
        
        # Capacity and cost settings"""

if target1 not in code:
    raise ValueError("Target 1 not found in code")
code = code.replace(target1, replacement1, 1)

# 2. Setup addition
target2 = """        # 4. Следим за переключением режима Auto/Manual
        async_track_state_change_event(
            self.hass,
            ["select.ems_boiler_mode"],
            self._async_system_mode_changed
        )

        # Применяем план при старте"""

replacement2 = """        # 4. Следим за переключением режима Auto/Manual
        async_track_state_change_event(
            self.hass,
            ["select.ems_boiler_mode"],
            self._async_system_mode_changed
        )

        # 5. Следим за изменением температуры для работы программного термостата и перекачки
        temp_entities = []
        if self.elec_temp:
            temp_entities.append(self.elec_temp)
        if self.gas_climate:
            temp_entities.append(self.gas_climate)
            
        if temp_entities:
            async_track_state_change_event(
                self.hass,
                temp_entities,
                self._async_temp_changed
            )

        # Применяем план при старте"""

if target2 not in code:
    raise ValueError("Target 2 not found in code")
code = code.replace(target2, replacement2, 1)

# 3. Add helper methods after _async_safety_check
target3 = """    async def _async_safety_check(self, event):
        \"\"\"Жёсткая аппаратная блокировка.
        valve OFF = электробойлер ИЗОЛИРОВАН → немедленно гасим насос.
        \"\"\"
        valve_state = self.hass.states.get(self.bypass_valve)

        if valve_state and valve_state.state == STATE_OFF:
            if self.pump:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.pump}
                )
            
    async def _async_override_check(self, event):"""

replacement3 = """    async def _async_safety_check(self, event):
        \"\"\"Жёсткая аппаратная блокировка.
        valve OFF = электробойлер ИЗОЛИРОВАН → немедленно гасим насос.
        \"\"\"
        valve_state = self.hass.states.get(self.bypass_valve)

        if valve_state and valve_state.state == STATE_OFF:
            if self.pump:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.pump}
                )
            
    def _update_cutoff_states(self):
        \"\"\"Обновление флагов отсечки нагрева и перекачки с учетом гистерезиса и режима Fail-Safe.\"\"\"
        # 1. Электробойлер
        t_elec = self._get_elec_temp()
        t_max_elec = float(self.config.get("elec_boiler_max_temp", 70.0))
        hysteresis = 2.0
        
        if t_elec is None:
            dp_state = self.hass.states.get("sensor.boiler_dp")
            mode = dp_state.state.upper() if dp_state else "IDLE"
            if "ELEC" in mode and self.current_mode.lower() == "auto":
                if not self._elec_cutoff_active:
                    _LOGGER.warning("EMS Boiler Controller: Temperature sensor for electric boiler is unavailable during active heating. Activating safety cutoff.")
                self._elec_cutoff_active = True
                self._elec_pump_dump_active = False
        else:
            # Общая остановка при достижении setpoint
            if t_elec >= t_max_elec:
                if not self._elec_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler reached setpoint (%.1f >= %.1f). Stopping heating.", t_elec, t_max_elec)
                self._elec_cutoff_active = True
            elif t_elec < t_max_elec - hysteresis:
                if self._elec_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler cooled below hysteresis (%.1f < %.1f). Resuming heating.", t_elec, t_max_elec - hysteresis)
                self._elec_cutoff_active = False

            # Управление насосом перекачки (включение при setpoint - 5, выключение при setpoint - 10)
            t_pump_on = t_max_elec - 5.0
            t_pump_off = t_max_elec - 10.0
            if t_elec >= t_pump_on:
                if not self._elec_pump_dump_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler temperature high (%.1f >= %.1f). Activating pump dump override.", t_elec, t_pump_on)
                self._elec_pump_dump_active = True
            elif t_elec < t_pump_off:
                if self._elec_pump_dump_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler cooled below dump threshold (%.1f < %.1f). Deactivating pump dump override.", t_elec, t_pump_off)
                self._elec_pump_dump_active = False

        # 2. Газовый котел
        t_gas = self._get_gas_temp()
        t_max_gas = float(self.config.get("gas_boiler_max_temp", 50.0))
        
        if t_gas is None:
            dp_state = self.hass.states.get("sensor.boiler_dp")
            mode = dp_state.state.upper() if dp_state else "IDLE"
            if "GAS" in mode and self.current_mode.lower() == "auto":
                if not self._gas_cutoff_active:
                    _LOGGER.warning("EMS Boiler Controller: Temperature sensor for gas boiler is unavailable during active heating. Activating safety cutoff.")
                self._gas_cutoff_active = True
        else:
            if t_gas >= t_max_gas:
                if not self._gas_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Gas boiler reached max temperature (%.1f >= %.1f). Activating cutoff.", t_gas, t_max_gas)
                self._gas_cutoff_active = True
            elif t_gas < t_max_gas - hysteresis:
                if self._gas_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Gas boiler cooled below hysteresis (%.1f < %.1f). Deactivating cutoff.", t_gas, t_max_gas - hysteresis)
                self._gas_cutoff_active = False

    async def _async_temp_changed(self, event):
        \"\"\"Обработка изменения температуры бойлеров.\"\"\"
        if self.current_mode.lower() == "auto":
            await self._async_apply_current_dp_plan()

    async def _async_override_check(self, event):"""

if target3 not in code:
    raise ValueError("Target 3 not found in code")
code = code.replace(target3, replacement3, 1)

# 4. Modify override checks
target4 = """                    if entity_id == self.elec_heater:
                        expected_state = STATE_ON if "ELEC" in mode else STATE_OFF
                    elif entity_id == self.pump:
                        expected_state = STATE_ON if ("_PUMP" in mode and recommended_bypass == "ON") else STATE_OFF"""

replacement4 = """                    if entity_id == self.elec_heater:
                        if "ELEC" in mode:
                            expected_state = STATE_OFF if self._elec_cutoff_active else STATE_ON
                        else:
                            expected_state = STATE_OFF
                    elif entity_id == self.pump:
                        if self._elec_pump_dump_active and "ELEC" in mode:
                            expected_state = STATE_ON
                        else:
                            expected_state = STATE_ON if ("_PUMP" in mode and recommended_bypass == "ON") else STATE_OFF"""

if target4 not in code:
    raise ValueError("Target 4 not found in code")
code = code.replace(target4, replacement4, 1)

# 5. Replace _async_set_boiler_mode method
target5 = """    async def _async_set_boiler_mode(self, mode: str, recommended_bypass: str):
        \"\"\"Turn on/off actuators based on mode and recommended bypass.\"\"\"
        self._is_applying_dp_plan = True
        try:
            # 1. Control Bypass Valve
            if self.bypass_valve:
                valve_domain = self.bypass_valve.split(".")[0]
                current_valve = self.hass.states.get(self.bypass_valve)
                target_service = SERVICE_TURN_ON if recommended_bypass == "ON" else SERVICE_TURN_OFF
                target_state = STATE_ON if recommended_bypass == "ON" else STATE_OFF
                if not current_valve or current_valve.state != target_state:
                    await self.hass.services.async_call(
                        valve_domain,
                        target_service,
                        {ATTR_ENTITY_ID: self.bypass_valve}
                    )

            # 2. Control Circulation Pump
            if self.pump:
                current_pump = self.hass.states.get(self.pump)
                target_pump_service = SERVICE_TURN_ON if "_PUMP" in mode else SERVICE_TURN_OFF
                target_pump_state = STATE_ON if "_PUMP" in mode else STATE_OFF
                # Safety check: bypass must be open to run pump
                if target_pump_service == SERVICE_TURN_ON and recommended_bypass != "ON":
                    _LOGGER.warning("EMS Boiler Controller: Prevented turning pump ON in Auto mode because bypass is closed")
                    target_pump_service = SERVICE_TURN_OFF
                    target_pump_state = STATE_OFF
                if not current_pump or current_pump.state != target_pump_state:
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_pump_service,
                        {ATTR_ENTITY_ID: self.pump}
                    )

            # 3. Control Electric Heater
            if self.elec_heater:
                current_heater = self.hass.states.get(self.elec_heater)
                target_heater_service = SERVICE_TURN_ON if "ELEC" in mode else SERVICE_TURN_OFF
                target_heater_state = STATE_ON if "ELEC" in mode else STATE_OFF
                if not current_heater or current_heater.state != target_heater_state:
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_heater_service,
                        {ATTR_ENTITY_ID: self.elec_heater}
                    )

            # 4. Control Gas Climate
            if self.gas_climate:
                current_gas = self.hass.states.get(self.gas_climate)
                target_hvac = "heat" if "GAS" in mode else "off"
                if not current_gas or current_gas.state != target_hvac:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        "set_hvac_mode",
                        {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": target_hvac}
                    )
                if target_hvac == "heat":
                    target_temp = float(self.config.get("gas_boiler_max_temp", 50.0))
                    current_target_temp = current_gas.attributes.get("temperature") if current_gas else None
                    if current_target_temp != target_temp:
                        await self.hass.services.async_call(
                            CLIMATE_DOMAIN,
                            "set_temperature",
                            {ATTR_ENTITY_ID: self.gas_climate, ATTR_TEMPERATURE: target_temp}
                        )"""

replacement5 = """    async def _async_set_boiler_mode(self, mode: str, recommended_bypass: str):
        \"\"\"Turn on/off actuators based on mode and recommended bypass.\"\"\"
        self._is_applying_dp_plan = True
        try:
            # Обновляем флаги программной отсечки по температуре
            self._update_cutoff_states()

            # 1. Control Bypass Valve
            if self.bypass_valve:
                valve_domain = self.bypass_valve.split(".")[0]
                current_valve = self.hass.states.get(self.bypass_valve)
                
                # Определяем целевое состояние байпаса:
                # - GAS -> Закрыт (OFF)
                # - Режимы с нагревом (ELEC, ELEC_PUMP, GAS_PUMP) -> Открыт (ON)
                # - Остальное (IDLE и др.) -> Сохраняется неизменным
                if mode == "GAS":
                    target_bypass = "OFF"
                elif "ELEC" in mode or "_PUMP" in mode:
                    target_bypass = "ON"
                else:
                    target_bypass = current_valve.state.upper() if current_valve else None

                if target_bypass in ("ON", "OFF"):
                    target_service = SERVICE_TURN_ON if target_bypass == "ON" else SERVICE_TURN_OFF
                    target_state = STATE_ON if target_bypass == "ON" else STATE_OFF
                    if not current_valve or current_valve.state != target_state:
                        _LOGGER.info("EMS Boiler Controller: Setting bypass valve from %s to %s", current_valve.state if current_valve else "unknown", target_state)
                        await self.hass.services.async_call(
                            valve_domain,
                            target_service,
                            {ATTR_ENTITY_ID: self.bypass_valve}
                        )

            # 2. Control Circulation Pump
            if self.pump:
                current_pump = self.hass.states.get(self.pump)
                
                # Принудительно включаем циркуляцию при аварийном сбросе тепла ТЭНа
                if self._elec_pump_dump_active and "ELEC" in mode:
                    target_pump_state_logical = True
                else:
                    target_pump_state_logical = "_PUMP" in mode

                target_pump_service = SERVICE_TURN_ON if target_pump_state_logical else SERVICE_TURN_OFF
                target_pump_state = STATE_ON if target_pump_state_logical else STATE_OFF
                
                # Safety check: bypass must be open to run pump
                if target_pump_service == SERVICE_TURN_ON and target_bypass != "ON":
                    _LOGGER.warning("EMS Boiler Controller: Prevented turning pump ON in Auto mode because bypass is closed")
                    target_pump_service = SERVICE_TURN_OFF
                    target_pump_state = STATE_OFF
                if not current_pump or current_pump.state != target_pump_state:
                    _LOGGER.info("EMS Boiler Controller: Setting circulation pump from %s to %s", current_pump.state if current_pump else "unknown", target_pump_state)
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_pump_service,
                        {ATTR_ENTITY_ID: self.pump}
                    )

            # 3. Control Electric Heater
            if self.elec_heater:
                current_heater = self.hass.states.get(self.elec_heater)
                target_heater_service = SERVICE_TURN_ON if ("ELEC" in mode and not self._elec_cutoff_active) else SERVICE_TURN_OFF
                target_heater_state = STATE_ON if ("ELEC" in mode and not self._elec_cutoff_active) else STATE_OFF
                if not current_heater or current_heater.state != target_heater_state:
                    _LOGGER.info("EMS Boiler Controller: Setting electric heater switch from %s to %s", current_heater.state if current_heater else "unknown", target_heater_state)
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_heater_service,
                        {ATTR_ENTITY_ID: self.elec_heater}
                    )

            # 4. Control Gas Climate
            if self.gas_climate:
                current_gas = self.hass.states.get(self.gas_climate)
                target_hvac = "heat" if ("GAS" in mode and not self._gas_cutoff_active) else "off"
                if not current_gas or current_gas.state != target_hvac:
                    _LOGGER.info("EMS Boiler Controller: Setting gas climate HVAC mode from %s to %s", current_gas.state if current_gas else "unknown", target_hvac)
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        "set_hvac_mode",
                        {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": target_hvac}
                    )
                if target_hvac == "heat":
                    target_temp = float(self.config.get("gas_boiler_max_temp", 50.0))
                    current_target_temp = current_gas.attributes.get("temperature") if current_gas else None
                    if current_target_temp != target_temp:
                        await self.hass.services.async_call(
                            CLIMATE_DOMAIN,
                            "set_temperature",
                            {ATTR_ENTITY_ID: self.gas_climate, ATTR_TEMPERATURE: target_temp}
                        )"""

if target5 not in code:
    raise ValueError("Target 5 not found in code")
code = code.replace(target5, replacement5, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Modifications applied successfully.")
