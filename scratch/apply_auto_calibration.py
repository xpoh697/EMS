import os

const_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\const.py"
config_flow_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\config_flow.py"
strings_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\strings.json"
en_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\translations\en.json"
ru_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\translations\ru.json"
controller_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\boiler_controller.py"

# --- 1. Modify const.py ---
with open(const_path, "r", encoding="utf-8") as f:
    const_content = f.read()

target_const = 'CONF_DEBUG = "debug"'
replacement_const = 'CONF_DEBUG = "debug"\nCONF_CALIBRATION_TYPE = "calibration_type"\nCONF_WATER_FLOW_SENSOR = "water_flow_sensor"'

if target_const in const_content:
    const_content = const_content.replace(target_const, replacement_const, 1)
    with open(const_path, "w", encoding="utf-8") as f:
        f.write(const_content)
    print("const.py updated successfully.")
else:
    print("Warning: target_const not found in const.py")

# --- 2. Modify config_flow.py ---
with open(config_flow_path, "r", encoding="utf-8") as f:
    cf_content = f.read()

# Add imports
target_cf_import = "    CONF_DEBUG,"
replacement_cf_import = "    CONF_DEBUG,\n    CONF_CALIBRATION_TYPE,\n    CONF_WATER_FLOW_SENSOR,"

if target_cf_import in cf_content:
    cf_content = cf_content.replace(target_cf_import, replacement_cf_import, 1)
else:
    print("Warning: target_cf_import not found in config_flow.py")

# Add form selectors
target_cf_selectors = """        schema_dict[vol.Required("gas_cost_m3", default=gas_cost)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, step=0.01, mode=selector.NumberSelectorMode.BOX)
        )"""

replacement_cf_selectors = """        schema_dict[vol.Required("gas_cost_m3", default=gas_cost)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.0, step=0.01, mode=selector.NumberSelectorMode.BOX)
        )

        # Select for calibration mode: CONF_CALIBRATION_TYPE
        cal_type = self._user_input.get(CONF_CALIBRATION_TYPE, "manual")
        schema_dict[vol.Required(CONF_CALIBRATION_TYPE, default=cal_type)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": "manual", "label": "Manual"},
                    {"value": "auto", "label": "Auto"},
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="calibration_type"
            )
        )

        # Optional: CONF_WATER_FLOW_SENSOR
        val_flow = get_value(CONF_WATER_FLOW_SENSOR)
        if val_flow:
            schema_dict[vol.Optional(CONF_WATER_FLOW_SENSOR, default=val_flow)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )
        else:
            schema_dict[vol.Optional(CONF_WATER_FLOW_SENSOR)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )"""

if target_cf_selectors in cf_content:
    cf_content = cf_content.replace(target_cf_selectors, replacement_cf_selectors, 1)
    with open(config_flow_path, "w", encoding="utf-8") as f:
        f.write(cf_content)
    print("config_flow.py updated successfully.")
else:
    print("Warning: target_cf_selectors not found in config_flow.py")

# --- 3. Modify strings.json & en.json ---
def update_json_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update data fields
    target_data = '"gas_cost_m3": "Cost per 1 m³ of gas"'
    replacement_data = '"gas_cost_m3": "Cost per 1 m³ of gas",\n          "calibration_type": "Standby loss calibration mode",\n          "water_flow_sensor": "Instantaneous water flow sensor"'

    # Update selector
    target_selector = """  "selector": {
    "category": {
      "options": {
        "basic_settings": "Basic settings",
        "pv_forecast": "PV Forecast",
        "financial": "Financial",
        "battery_optimization": "Battery optimization",
        "boiler": "Boiler Configuration"
      }
    }
  },"""

    replacement_selector = """  "selector": {
    "category": {
      "options": {
        "basic_settings": "Basic settings",
        "pv_forecast": "PV Forecast",
        "financial": "Financial",
        "battery_optimization": "Battery optimization",
        "boiler": "Boiler Configuration"
      }
    },
    "calibration_type": {
      "options": {
        "manual": "Manual",
        "auto": "Auto"
      }
    }
  },"""

    if target_data in content:
        content = content.replace(target_data, replacement_data, 1)
    else:
        print(f"Warning: target_data not found in {file_path}")

    if target_selector in content:
        content = content.replace(target_selector, replacement_selector, 1)
    else:
        print(f"Warning: target_selector not found in {file_path}")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"{os.path.basename(file_path)} updated successfully.")

update_json_file(strings_path)
update_json_file(en_path)

# --- 4. Modify ru.json ---
with open(ru_path, "r", encoding="utf-8") as f:
    ru_content = f.read()

target_ru_data = '"gas_cost_m3": "Стоимость за 1 м³ газа"'
replacement_ru_data = '"gas_cost_m3": "Стоимость за 1 м³ газа",\n          "calibration_type": "Режим калибровки остывания",\n          "water_flow_sensor": "Датчик моментального расхода воды"'

target_ru_selector = """  "selector": {
    "category": {
      "options": {
        "basic_settings": "Основные настройки",
        "pv_forecast": "Прогноз СЭС",
        "financial": "Финансы",
        "battery_optimization": "Оптимизация АКБ",
        "boiler": "Настройки Бойлера"
      }
    }
  },"""

replacement_ru_selector = """  "selector": {
    "category": {
      "options": {
        "basic_settings": "Основные настройки",
        "pv_forecast": "Прогноз СЭС",
        "financial": "Финансы",
        "battery_optimization": "Оптимизация АКБ",
        "boiler": "Настройки Бойлера"
      }
    },
    "calibration_type": {
      "options": {
        "manual": "Ручной",
        "auto": "Автоматический"
      }
    }
  },"""

if target_ru_data in ru_content:
    ru_content = ru_content.replace(target_ru_data, replacement_ru_data, 1)
else:
    print("Warning: target_ru_data not found in ru.json")

if target_ru_selector in ru_content:
    ru_content = ru_content.replace(target_ru_selector, replacement_ru_selector, 1)
else:
    print("Warning: target_ru_selector not found in ru.json")

with open(ru_path, "w", encoding="utf-8") as f:
    f.write(ru_content)
print("ru.json updated successfully.")

# --- 5. Modify boiler_controller.py ---
with open(controller_path, "r", encoding="utf-8") as f:
    ctrl_content = f.read()

# Add imports
target_ctrl_import = "from homeassistant.util import dt as dt_util"
replacement_ctrl_import = "from homeassistant.util import dt as dt_util\nfrom .const import CONF_CALIBRATION_TYPE, CONF_WATER_FLOW_SENSOR"

if target_ctrl_import in ctrl_content:
    ctrl_content = ctrl_content.replace(target_ctrl_import, replacement_ctrl_import, 1)
else:
    print("Warning: target_ctrl_import not found in boiler_controller.py")

# Add helper methods and _async_auto_standby_poll before overnight_start
target_ctrl_overnight = "    # =========================================================================\n    # Passive Overnight Thermal Loss Calibration (01:00 AM - 05:00 AM)"
replacement_ctrl_overnight = """    def _is_water_flowing(self) -> bool:
        flow_sensor = self.config.get(CONF_WATER_FLOW_SENSOR)
        if not flow_sensor:
            return False
        state = self.hass.states.get(flow_sensor)
        if not state or state.state in ("unknown", "unavailable"):
            return False
        if flow_sensor.startswith("binary_sensor."):
            return state.state == STATE_ON
        try:
            return float(state.state) > 0.1
        except (ValueError, TypeError):
            return False

    def _is_gvs_pump_active(self) -> bool:
        from .const import CONF_HW_CIRCULATION_PUMP
        pump_sensor = self.config.get(CONF_HW_CIRCULATION_PUMP)
        if not pump_sensor:
            return False
        state = self.hass.states.get(pump_sensor)
        return state is not None and state.state == STATE_ON

    def _is_any_heating_active(self) -> bool:
        if self.elec_heater:
            state = self.hass.states.get(self.elec_heater)
            if state and state.state == STATE_ON:
                return True
        if self.gas_climate:
            state = self.hass.states.get(self.gas_climate)
            if state and state.state in ("heat", "on"):
                return True
        if self.pump:
            state = self.hass.states.get(self.pump)
            if state and state.state == STATE_ON:
                return True
        return False

    def _is_auto_standby_enabled(self) -> bool:
        cal_type = self.config.get(CONF_CALIBRATION_TYPE, "manual")
        flow_sensor = self.config.get(CONF_WATER_FLOW_SENSOR)
        return cal_type == "auto" and flow_sensor not in (None, "", "undefined")

    def _init_auto_standby_boiler_state(self, temp: float) -> dict | None:
        if temp is None:
            return None
        import math
        bracket_top = math.ceil(temp / 5.0) * 5.0
        if temp == bracket_top:
            bracket_top += 5.0
        return {
            "bracket_top": bracket_top,
            "bracket_entered_at": dt_util.now().isoformat(),
            "start_temp": temp,
            "prev_temp": temp,
        }

    def _check_init_auto_standby(self):
        if not hasattr(self, "_auto_standby_state") or not self._auto_standby_state:
            self._auto_standby_state = {}
        
        gas_temp = self._get_gas_temp()
        elec_temp = self._get_elec_temp()
        
        if "gas" not in self._auto_standby_state and gas_temp is not None and gas_temp > 20.0:
            self._auto_standby_state["gas"] = self._init_auto_standby_boiler_state(gas_temp)
        if "elec" not in self._auto_standby_state and elec_temp is not None and elec_temp > 20.0:
            self._auto_standby_state["elec"] = self._init_auto_standby_boiler_state(elec_temp)

    def _handle_auto_standby_interruption(self, boiler: str, t_curr: float, reason: str):
        state = self._auto_standby_state.get(boiler)
        if state is None:
            return
            
        start_temp = state["start_temp"]
        entered_at_str = state["bracket_entered_at"]
        entered_at = dt_util.parse_datetime(entered_at_str)
        
        if entered_at:
            elapsed_h = (dt_util.now() - entered_at).total_seconds() / 3600.0
        else:
            elapsed_h = 0.0
            
        temp_drop = start_temp - t_curr
        
        # Save as partial bracket if lasted >= 20 mins and temp drop >= 0.2°C
        if elapsed_h >= (20.0 / 60.0) and temp_drop >= 0.2:
            rate = round(temp_drop / elapsed_h, 4)
            t_mid = (start_temp + t_curr) / 2.0
            
            import math
            b_top = math.ceil(t_mid / 5.0) * 5.0
            if t_mid == b_top:
                b_top += 5.0
            b_bottom = b_top - 5.0
            
            if b_bottom >= 20.0:
                key = self._get_bracket_key(b_top, b_bottom)
                standby = self.calibration_sensor.get_standby_losses()
                old_rate = standby.get(boiler, {}).get(key, 0.0)
                new_rate = self._apply_ema(old_rate, rate, alpha=0.1)
                
                self.calibration_sensor.update_calibration_coefficient(
                    "overnight_loss",
                    {
                        boiler: {key: new_rate},
                        "last_calibrated": dt_util.now().date().isoformat()
                    }
                )
                _LOGGER.info(
                    "[auto/%s] Interrupted by %s. Saved partial bracket %s: %.4f °C/h (EMA alpha=0.1, drop=%.2f°C, elapsed=%.2fh)",
                    boiler, reason, key, new_rate, temp_drop, elapsed_h
                )
        else:
            _LOGGER.debug(
                "[auto/%s] Interrupted by %s. Discarded: elapsed=%.2fh, drop=%.2f°C",
                boiler, reason, elapsed_h, temp_drop
            )
            
        self._auto_standby_state[boiler] = self._init_auto_standby_boiler_state(t_curr)

    async def _async_auto_standby_poll(self):
        if not self.calibration_sensor:
            return
            
        if self.calibration_sensor.native_value != "idle":
            self._auto_standby_state = {}
            return
            
        self._check_init_auto_standby()
        
        is_flow = self._is_water_flowing()
        is_gvs = self._is_gvs_pump_active()
        is_heat = self._is_any_heating_active()
        
        readings = {
            "gas": self._get_gas_temp(),
            "elec": self._get_elec_temp(),
        }
        
        for boiler, t_curr in readings.items():
            state = self._auto_standby_state.get(boiler)
            if state is None or t_curr is None:
                continue
                
            t_prev = state["prev_temp"]
            
            interrupted = False
            reason = ""
            if is_flow:
                interrupted = True
                reason = "water flow"
            elif is_gvs:
                interrupted = True
                reason = "GVS pump active"
            elif is_heat:
                interrupted = True
                reason = "active heating"
            elif abs(t_curr - t_prev) > 2.0:
                interrupted = True
                reason = f"temp jump {t_prev:.1f}->{t_curr:.1f}°C"
                
            if interrupted:
                self._handle_auto_standby_interruption(boiler, t_curr, reason)
                continue
                
            bracket_top = state["bracket_top"]
            bracket_bottom = bracket_top - 5.0
            
            if t_curr <= bracket_bottom and bracket_bottom >= 20.0:
                start_temp = state["start_temp"]
                entered_at_str = state["bracket_entered_at"]
                entered_at = dt_util.parse_datetime(entered_at_str)
                if entered_at:
                    elapsed_h = (dt_util.now() - entered_at).total_seconds() / 3600.0
                else:
                    elapsed_h = 0.0
                    
                temp_drop = start_temp - t_curr
                if elapsed_h >= 0.1 and temp_drop > 0.0:
                    rate = round(temp_drop / elapsed_h, 4)
                    key = self._get_bracket_key(bracket_top, bracket_bottom)
                    standby = self.calibration_sensor.get_standby_losses()
                    old_rate = standby.get(boiler, {}).get(key, 0.0)
                    new_rate = self._apply_ema(old_rate, rate, alpha=0.1)
                    
                    self.calibration_sensor.update_calibration_coefficient(
                        "overnight_loss",
                        {
                            boiler: {key: new_rate},
                            "last_calibrated": dt_util.now().date().isoformat()
                        }
                    )
                    _LOGGER.info(
                        "[auto/%s] Completed full bracket %s: %.4f °C/h (EMA alpha=0.1, elapsed=%.2fh)",
                        boiler, key, new_rate, elapsed_h
                    )
                
                state["bracket_top"] = bracket_bottom
                state["bracket_entered_at"] = dt_util.now().isoformat()
                state["start_temp"] = t_curr
                
            state["prev_temp"] = t_curr

    # =========================================================================
    # Passive Overnight Thermal Loss Calibration (01:00 AM - 05:00 AM)"""

if target_ctrl_overnight in ctrl_content:
    ctrl_content = ctrl_content.replace(target_ctrl_overnight, replacement_ctrl_overnight, 1)
else:
    print("Warning: target_ctrl_overnight not found in boiler_controller.py")

# Overnight start/end/poll check additions
target_overnight_start = """    async def _async_overnight_start(self, _now=None):
        \"\"\"Запуск ночного мониторинга в 01:00 — инициализация state machine.\"\"\"
        if not self.calibration_sensor:
            return"""

replacement_overnight_start = """    async def _async_overnight_start(self, _now=None):
        \"\"\"Запуск ночного мониторинга в 01:00 — инициализация state machine.\"\"\"
        if self._is_auto_standby_enabled():
            return

        if not self.calibration_sensor:
            return"""

if target_overnight_start in ctrl_content:
    ctrl_content = ctrl_content.replace(target_overnight_start, replacement_overnight_start, 1)
else:
    print("Warning: target_overnight_start not found in boiler_controller.py")

target_overnight_poll = """    async def _async_overnight_poll(self, _now=None):
        \"\"\"Ежеминутный polling (01:00–05:00): обнаружение пересечений брэкетов.\"\"\"
        if not self.calibration_sensor:
            return"""

replacement_overnight_poll = """    async def _async_overnight_poll(self, _now=None):
        \"\"\"Ежеминутный polling (01:00–05:00): обнаружение пересечений брэкетов.\"\"\"
        if self._is_auto_standby_enabled():
            await self._async_auto_standby_poll()
            return

        if not self.calibration_sensor:
            return"""

if target_overnight_poll in ctrl_content:
    ctrl_content = ctrl_content.replace(target_overnight_poll, replacement_overnight_poll, 1)
else:
    print("Warning: target_overnight_poll not found in boiler_controller.py")

target_overnight_end = """    async def _async_overnight_end(self, _now=None):
        \"\"\"Завершение ночного мониторинга в 05:00 — финализация LUT.\"\"\"
        if not self.calibration_sensor or self.calibration_sensor.native_value != "overnight_loss":
            return"""

replacement_overnight_end = """    async def _async_overnight_end(self, _now=None):
        \"\"\"Завершение ночного мониторинга в 05:00 — финализация LUT.\"\"\"
        if self._is_auto_standby_enabled():
            return

        if not self.calibration_sensor or self.calibration_sensor.native_value != "overnight_loss":
            return"""

if target_overnight_end in ctrl_content:
    ctrl_content = ctrl_content.replace(target_overnight_end, replacement_overnight_end, 1)
else:
    print("Warning: target_overnight_end not found in boiler_controller.py")

# Update execute_heating_phase logic with autorestarts and cooling wait
target_exec_phase = """    async def _async_execute_heating_phase(self, phase: str, cal_data: dict):
        \"\"\"Фоновый процесс циклического нагрева, стабилизации и финализации.\"\"\"
        t_start = cal_data["t_start"]
        success = False
        
        if "pump" in phase:
            # Фазы с насосом: нагрев в течение заданного времени (по умолчанию 5 минут)
            duration_minutes = cal_data.get("heating_duration_minutes") or 5
            duration = duration_minutes * 60
            _LOGGER.info("Starting active calibration phase: %s. Baseline Temp: %s°C, Duration: %ss", phase, t_start, duration)
            await self._actuate_heating(phase, turn_on=True, target_temp=80.0)
            
            elapsed = 0
            success = True
            try:
                # Начальный статус перед циклом
                cal_data["status_desc"] = f"Нагрев {duration_minutes} мин"
                cal_data["time_left"] = duration
                self.calibration_sensor.set_calibration_state(phase, cal_data)

                while elapsed < duration:
                    await asyncio.sleep(10)
                    elapsed += 10
                    if "elec" in phase:
                        p_val = self._get_elec_power()
                        if p_val is not None:
                            if "power_readings" not in cal_data:
                                cal_data["power_readings"] = []
                            cal_data["power_readings"].append(p_val)
                    t_curr = self._get_system_temp()
                    _LOGGER.info("[%s] Heating in progress... Elapsed: %ss/%ss, Current Temp: %s°C", phase, elapsed, duration, t_curr)
                    
                    cal_data["status_desc"] = f"Нагрев {duration_minutes} мин"
                    cal_data["time_left"] = max(0, duration - elapsed)
                    self.calibration_sensor.set_calibration_state(phase, cal_data)
            except Exception as ex:
                _LOGGER.error("Error during calibration heating loop: %s", ex)
                success = False
        else:
            # Одиночные фазы без насоса: нагрев до достижения целевой температуры (по умолчанию T_start + 12.0°C)
            delta = cal_data.get("target_temperature_delta") or 12.0
            t_target = t_start + delta
            _LOGGER.info("Starting active calibration phase: %s. Baseline Temp: %s°C, Target Temp: %s°C", phase, t_start, t_target)
            await self._actuate_heating(phase, turn_on=True, target_temp=t_target)
            
            timeout = 5400  # 90 минут в секундах
            elapsed = 0
            try:
                # Начальный статус перед циклом
                cal_data["status_desc"] = f"Нагрев до {t_target:.1f}°C"
                cal_data["time_left"] = None
                self.calibration_sensor.set_calibration_state(phase, cal_data)

                while elapsed < timeout:
                    await asyncio.sleep(10)
                    elapsed += 10
                    if "elec" in phase:
                        p_val = self._get_elec_power()
                        if p_val is not None:
                            if "power_readings" not in cal_data:
                                cal_data["power_readings"] = []
                            cal_data["power_readings"].append(p_val)
                    
                    if "gas" in phase:
                        t_curr = self._get_gas_temp()
                    else:
                        t_curr = self._get_elec_temp()
                        
                    _LOGGER.info("[%s] Heating in progress... Elapsed: %ss/%ss, Current Temp: %s°C, Target Temp: %s°C", phase, elapsed, timeout, t_curr, t_target)
                    
                    cal_data["status_desc"] = f"Нагрев до {t_target:.1f}°C"
                    cal_data["time_left"] = None
                    self.calibration_sensor.set_calibration_state(phase, cal_data)
                    
                    if t_curr is not None and t_curr >= t_target:
                        success = True
                        break
            except Exception as ex:
                _LOGGER.error("Error during calibration heating loop: %s", ex)
                success = False

        # 3. Выключение нагревателей и насосов
        await self._actuate_heating(phase, turn_on=False)

        if not success:
            _LOGGER.error("Calibration phase %s aborted: safety timeout (90 min) reached or heating failed.", phase)
            self.calibration_sensor.set_calibration_state("idle", {})
            return

        # 4. Стабилизация: 10 минут (или из настроек) для всех фаз
        stab_minutes = cal_data.get("stabilization_minutes") or 10
        stab_duration = float(stab_minutes * 60)
        _LOGGER.info("Heating target reached. Starting %s-minute stabilization delay...", stab_minutes)
        cal_data["heating_ended_at"] = dt_util.now().isoformat()
        cal_data["stabilization_duration"] = stab_duration
        cal_data["status_desc"] = f"Стабилизация {stab_minutes} мин"
        cal_data["time_left"] = int(stab_duration)
        self.calibration_sensor.set_calibration_state(phase, cal_data)

        await self._async_stabilize_and_finalize(phase, cal_data, stab_duration)"""

replacement_exec_phase = """    def _is_temp_too_high_for_calibration(self, phase: str, target_delta: float) -> bool:
        if "gas" in phase:
            t_curr = self._get_gas_temp()
            t_max_limit = float(self.config.get("gas_boiler_max_temp", 50.0))
        else:
            t_curr = self._get_elec_temp()
            t_max_limit = float(self.config.get("elec_boiler_max_temp", 70.0))
            
        if t_curr is None:
            return False
            
        return t_curr >= (t_max_limit - target_delta - 2.0)

    async def _async_execute_heating_phase(self, phase: str, cal_data: dict):
        \"\"\"Фоновый процесс циклического нагрева, стабилизации и финализации с поддержкой авторестартов.\"\"\"
        attempt = 1
        max_attempts = 3 if self.config.get(CONF_CALIBRATION_TYPE, "manual") == "auto" else 1
        
        while attempt <= max_attempts:
            target_delta = cal_data.get("target_temperature_delta") or 12.0
            
            # 1. Ожидание остывания, если температура слишком высокая (только в режиме auto)
            if self.config.get(CONF_CALIBRATION_TYPE, "manual") == "auto":
                while self._is_temp_too_high_for_calibration(phase, target_delta):
                    await self._actuate_heating(phase, turn_on=False)
                    cal_data["status_desc"] = f"Ожидание остывания (Попытка {attempt}/{max_attempts})"
                    cal_data["time_left"] = None
                    self.calibration_sensor.set_calibration_state(phase, cal_data)
                    await asyncio.sleep(10)
                    if not self.calibration_sensor or self.calibration_sensor.native_value == "idle":
                        return

            # Обновляем базовые показания датчиков на старте попытки
            try:
                baseline = self._get_baseline_readings(phase)
                cal_data["t_start"] = baseline["t_start"]
                cal_data["v_start"] = baseline.get("v_start")
                cal_data["e_start"] = baseline.get("e_start")
                cal_data["started_at"] = dt_util.now().isoformat()
                if "power_readings" in cal_data:
                    cal_data["power_readings"] = []
            except ValueError as err:
                _LOGGER.error("Calibration baseline readings failed for attempt %d: %s", attempt, err)
                if attempt >= max_attempts:
                    self.calibration_sensor.set_calibration_state("idle", {})
                    return
                attempt += 1
                await asyncio.sleep(5)
                continue

            t_start = cal_data["t_start"]
            attempt_failed = False
            attempt_failed_reason = ""
            
            if "pump" in phase:
                duration_minutes = cal_data.get("heating_duration_minutes") or 5
                duration = duration_minutes * 60
                _LOGGER.info("Starting active calibration phase: %s (Attempt %d/%d). Baseline Temp: %s°C, Duration: %ss", phase, attempt, max_attempts, t_start, duration)
                await self._actuate_heating(phase, turn_on=True, target_temp=80.0)
                
                elapsed = 0
                try:
                    cal_data["status_desc"] = f"Нагрев {duration_minutes} мин (Попытка {attempt}/{max_attempts})"
                    cal_data["time_left"] = duration
                    self.calibration_sensor.set_calibration_state(phase, cal_data)

                    while elapsed < duration:
                        await asyncio.sleep(10)
                        elapsed += 10
                        if "elec" in phase:
                            p_val = self._get_elec_power()
                            if p_val is not None:
                                if "power_readings" not in cal_data:
                                    cal_data["power_readings"] = []
                                cal_data["power_readings"].append(p_val)
                        t_curr = self._get_system_temp()
                        _LOGGER.info("[%s Attempt %d] Heating... Elapsed: %ss/%ss, Temp: %s°C", phase, attempt, elapsed, duration, t_curr)
                        
                        # Проверка на прерывание водозабором или насосом ГВС
                        if self.config.get(CONF_CALIBRATION_TYPE, "manual") == "auto":
                            if self._is_water_flowing() or self._is_gvs_pump_active():
                                _LOGGER.warning("[%s Attempt %d] Water draw or GVS pump active during heating. Retrying...", phase, attempt)
                                attempt_failed = True
                                attempt_failed_reason = "water_draw_or_gvs_pump"
                                break
                        
                        cal_data["status_desc"] = f"Нагрев {duration_minutes} мин (Попытка {attempt}/{max_attempts})"
                        cal_data["time_left"] = max(0, duration - elapsed)
                        self.calibration_sensor.set_calibration_state(phase, cal_data)
                except Exception as ex:
                    _LOGGER.error("Error during calibration heating loop: %s", ex)
                    attempt_failed = True
                    attempt_failed_reason = "exception"
            else:
                # Одиночные фазы без насоса
                delta = cal_data.get("target_temperature_delta") or 12.0
                t_target = t_start + delta
                _LOGGER.info("Starting active calibration phase: %s (Attempt %d/%d). Baseline Temp: %s°C, Target Temp: %s°C", phase, attempt, max_attempts, t_start, t_target)
                await self._actuate_heating(phase, turn_on=True, target_temp=t_target)
                
                timeout = 5400  # 90 минут
                elapsed = 0
                try:
                    cal_data["status_desc"] = f"Нагрев до {t_target:.1f}°C (Попытка {attempt}/{max_attempts})"
                    cal_data["time_left"] = None
                    self.calibration_sensor.set_calibration_state(phase, cal_data)

                    while elapsed < timeout:
                        await asyncio.sleep(10)
                        elapsed += 10
                        if "elec" in phase:
                            p_val = self._get_elec_power()
                            if p_val is not None:
                                if "power_readings" not in cal_data:
                                    cal_data["power_readings"] = []
                                cal_data["power_readings"].append(p_val)
                        
                        if "gas" in phase:
                            t_curr = self._get_gas_temp()
                        else:
                            t_curr = self._get_elec_temp()
                            
                        _LOGGER.info("[%s Attempt %d] Heating... Elapsed: %ss/%ss, Temp: %s°C, Target: %s°C", phase, attempt, elapsed, timeout, t_curr, t_target)
                        
                        if self.config.get(CONF_CALIBRATION_TYPE, "manual") == "auto":
                            if self._is_water_flowing() or self._is_gvs_pump_active():
                                _LOGGER.warning("[%s Attempt %d] Water draw or GVS pump active during heating. Retrying...", phase, attempt)
                                attempt_failed = True
                                attempt_failed_reason = "water_draw_or_gvs_pump"
                                break
                                
                        cal_data["status_desc"] = f"Нагрев до {t_target:.1f}°C (Попытка {attempt}/{max_attempts})"
                        cal_data["time_left"] = None
                        self.calibration_sensor.set_calibration_state(phase, cal_data)
                        
                        if t_curr is not None and t_curr >= t_target:
                            break
                    else:
                        if not attempt_failed:
                            _LOGGER.error("Calibration phase %s aborted: safety timeout (90 min) reached.", phase)
                            attempt_failed = True
                            attempt_failed_reason = "timeout"
                except Exception as ex:
                    _LOGGER.error("Error during calibration heating loop: %s", ex)
                    attempt_failed = True
                    attempt_failed_reason = "exception"

            # Выключаем нагрев
            await self._actuate_heating(phase, turn_on=False)

            if attempt_failed:
                if attempt_failed_reason == "water_draw_or_gvs_pump" and attempt < max_attempts:
                    attempt += 1
                    await asyncio.sleep(5)
                    continue
                else:
                    self.calibration_sensor.set_calibration_state("idle", {})
                    return

            # 4. Стабилизация
            stab_minutes = cal_data.get("stabilization_minutes") or 10
            stab_duration = float(stab_minutes * 60)
            _LOGGER.info("Heating target reached. Starting %s-minute stabilization delay...", stab_minutes)
            cal_data["heating_ended_at"] = dt_util.now().isoformat()
            cal_data["stabilization_duration"] = stab_duration
            cal_data["status_desc"] = f"Стабилизация {stab_minutes} мин (Попытка {attempt}/{max_attempts})"
            cal_data["time_left"] = int(stab_duration)
            self.calibration_sensor.set_calibration_state(phase, cal_data)

            # Выполняем стабилизацию с отслеживанием прерываний
            stab_failed = False
            elapsed_stab = 0
            while elapsed_stab < stab_duration:
                cal_data["status_desc"] = f"Стабилизация {stab_minutes} мин (Попытка {attempt}/{max_attempts})"
                cal_data["time_left"] = int(max(0, stab_duration - elapsed_stab))
                self.calibration_sensor.set_calibration_state(phase, cal_data)
                
                step = min(10.0, stab_duration - elapsed_stab)
                await asyncio.sleep(step)
                elapsed_stab += step
                
                if self.config.get(CONF_CALIBRATION_TYPE, "manual") == "auto":
                    if self._is_water_flowing() or self._is_gvs_pump_active():
                        _LOGGER.warning("[%s Attempt %d] Water draw or GVS pump active during stabilization. Retrying...", phase, attempt)
                        stab_failed = True
                        break
            
            if stab_failed:
                if attempt < max_attempts:
                    attempt += 1
                    await asyncio.sleep(5)
                    continue
                else:
                    self.calibration_sensor.set_calibration_state("idle", {})
                    return

            # Финализация
            await self._async_stabilize_and_finalize(phase, cal_data, 0.0)
            return"""

if target_exec_phase in ctrl_content:
    ctrl_content = ctrl_content.replace(target_exec_phase, replacement_exec_phase, 1)
    with open(controller_path, "w", encoding="utf-8") as f:
        f.write(ctrl_content)
    print("boiler_controller.py updated successfully.")
else:
    print("Warning: target_exec_phase not found in boiler_controller.py")
