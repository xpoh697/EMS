import datetime
import logging
import asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    async_track_time_change,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON, STATE_OFF
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

class BoilerController:
    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        
        self.elec_heater = config.get("elec_boiler_heater")
        self.pump = config.get("circulation_pump")
        self.bypass_valve = config.get("bypass_valve")
        
        # Calibration elements
        self.gas_climate = config.get("gas_boiler_climate")
        self.gas_meter = config.get("gas_boiler_meter")
        self.elec_energy = config.get("elec_boiler_energy")
        self.elec_temp = config.get("elec_boiler_temp")
        self.elec_power = config.get("elec_boiler_power")
        
        # Sensor reference (will be registered by sensor.py)
        self.calibration_sensor = None
        
        # Mode property, will be updated by EmsBoilerModeSelect
        self.current_mode = "Auto"
        
        # Флаги программной отсечки нагрева и перекачки тепла
        self._elec_cutoff_active = False
        self._gas_cutoff_active = False
        self._elec_pump_dump_active = False
        
        # Capacity and cost settings
        self.gas_capacity = config.get("gas_boiler_capacity", 100)
        self.elec_capacity = config.get("elec_boiler_capacity", 100)
        self.gas_cost_m3 = config.get("gas_cost_m3", 0.0)
        
    async def async_setup(self):
        """Регистрация безопасных слушателей событий и таймеров калибровки."""
        self._is_applying_dp_plan = False

        # 1. Мгновенный Safety Interlock: следим за клапаном
        if self.bypass_valve:
            async_track_state_change_event(
                self.hass, 
                [self.bypass_valve], 
                self._async_safety_check
            )
        
        # 2. Защита от ручного включения в режиме Auto
        if self.elec_heater and self.pump:
            async_track_state_change_event(
                self.hass,
                [self.elec_heater, self.pump],
                self._async_override_check
            )

        # 3. Следим за изменениями планировщика DP для автоматического управления
        async_track_state_change_event(
            self.hass,
            ["sensor.boiler_dp"],
            self._async_dp_plan_changed
        )

        # 4. Следим за переключением режима Auto/Manual
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

        # Применяем план при старте
        self.hass.async_create_task(self._async_apply_current_dp_plan())
        
        # 3. Throttling вычислений стоимости (раз в 2 минуты)
        async_track_time_interval(
            self.hass,
            self._async_calculate_costs,
            datetime.timedelta(minutes=2)
        )
        
        # 4. Polling ночного мониторинга охлаждения (каждую минуту)
        async_track_time_interval(
            self.hass,
            self._async_overnight_poll,
            datetime.timedelta(minutes=1)
        )
        
        # 5. Регистрация старта/завершения ночного теста (01:00 и 05:00)
        async_track_time_change(
            self.hass,
            self._async_overnight_start,
            hour=1,
            minute=0,
            second=0
        )
        async_track_time_change(
            self.hass,
            self._async_overnight_end,
            hour=5,
            minute=0,
            second=0
        )
        
    async def _async_safety_check(self, event):
        """Жёсткая аппаратная блокировка.
        valve OFF = электробойлер ИЗОЛИРОВАН → немедленно гасим насос.
        """
        valve_state = self.hass.states.get(self.bypass_valve)

        if valve_state and valve_state.state == STATE_OFF:
            if self.pump:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.pump}
                )
            
    def _update_cutoff_states(self):
        """Обновление флагов отсечки нагрева и перекачки с учетом гистерезиса и режима Fail-Safe."""
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
        """Обработка изменения температуры бойлеров."""
        if self.current_mode.lower() == "auto":
            await self._async_apply_current_dp_plan()

    async def _async_override_check(self, event):
        """Отклоняет ручные изменения в режиме Auto или если клапан перекрыт."""
        # Пропускаем любые проверки, если сейчас выполняется калибровка или применяется DP-план
        if getattr(self, "_is_applying_dp_plan", False):
            return

        if self.calibration_sensor and self.calibration_sensor.native_value != "idle":
            return

        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if not self.bypass_valve:
            return
            
        valve_state = self.hass.states.get(self.bypass_valve)
        
        # Блокировка 1: Режим Auto запрещает ручное управление
        if self.current_mode.lower() == "auto":
            if new_state and old_state and new_state.state != old_state.state:
                # Определим плановое состояние устройства на основе сенсора DP
                dp_state = self.hass.states.get("sensor.boiler_dp")
                if not dp_state or dp_state.state in ("unknown", "unavailable", "error", "idle_bypass", "NO PATH", "NO CALIB DATA"):
                    expected_state = STATE_OFF
                else:
                    mode = dp_state.state.upper()
                    recommended_bypass = dp_state.attributes.get("recommended_bypass", "OFF").upper()
                    if entity_id == self.elec_heater:
                        if "ELEC" in mode:
                            expected_state = STATE_OFF if self._elec_cutoff_active else STATE_ON
                        else:
                            expected_state = STATE_OFF
                    elif entity_id == self.pump:
                        if self._elec_pump_dump_active and "ELEC" in mode:
                            expected_state = STATE_ON
                        else:
                            expected_state = STATE_ON if ("_PUMP" in mode and recommended_bypass == "ON") else STATE_OFF
                    else:
                        expected_state = None

                # Если новое состояние совпадает с целевым плановым — пропускаем!
                if expected_state is not None and new_state.state == expected_state:
                    return

                # Если не совпадает — откатываем к плановому (или предыдущему)
                target_state = expected_state if expected_state is not None else old_state.state
                service = "turn_on" if target_state == STATE_ON else "turn_off"
                await self.hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id})

        # Блокировка 2: Попытка включить насос при изолированном электробойлере (valve OFF)
        elif entity_id == self.pump and valve_state and valve_state.state == STATE_OFF and new_state and new_state.state == STATE_ON:
            await self.hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})
                
    async def _async_calculate_costs(self, _now):
        """Real-time расчёт стоимости стендбай-потерь через LUT текущего брэкета."""
        if not self.calibration_sensor:
            return

        standby = self.calibration_sensor.get_standby_losses()
        gas_lut  = standby.get("gas", {})
        elec_lut = standby.get("elec", {})

        costs = {}

        # --- Газовый бойлер ---
        t_gas = self._get_gas_temp()
        if t_gas is not None:
            rate_gas = self._get_lut_rate(gas_lut, t_gas)       # °C/h
            eff_gas  = self.calibration_sensor.get_gas_efficiency()  # °C/m³
            if rate_gas is not None and eff_gas and eff_gas > 0:
                m3_per_h = rate_gas / eff_gas
                costs["gas_standby_cost_24h"] = round(m3_per_h * 24 * self.gas_cost_m3, 4)
                costs["gas_standby_rate_c_h"]  = round(rate_gas, 4)

        # --- Электрический бойлер ---
        t_elec = self._get_elec_temp()
        if t_elec is not None:
            rate_elec = self._get_lut_rate(elec_lut, t_elec)    # °C/h
            costs["elec_standby_rate_c_h"] = round(rate_elec, 4) if rate_elec is not None else 0.0
            # Финансовая модель по электро добавляется когда появится тариф ЦЕН/кВт

        if costs:
            self.calibration_sensor.update_standby_costs(costs)

    # =========================================================================
    # Passive Overnight Thermal Loss Calibration (01:00 AM - 05:00 AM)
    # Newton's Law LUT — температурно-брэкетная таблица 5°C шаг
    # =========================================================================

    # Состояние state machine для overnight мониторинга (in-memory, не персистентное)
    _overnight_state: dict = {}   # {"gas": {...}, "elec": {...}}
    _overnight_pending: dict = {} # накопленные обновления LUT до финализации

    async def _async_overnight_start(self, _now=None):
        """Запуск ночного мониторинга в 01:00 — инициализация state machine."""
        if not self.calibration_sensor:
            return

        if self.calibration_sensor.native_value != "idle":
            _LOGGER.warning(
                "Overnight loss calibration skipped: calibration already running (%s)",
                self.calibration_sensor.native_value,
            )
            return

        gas_temp  = self._get_gas_temp()
        elec_temp = self._get_elec_temp()

        gas_active  = gas_temp  is not None and gas_temp  > 20.0
        elec_active = elec_temp is not None and elec_temp > 20.0

        if not gas_active and not elec_active:
            _LOGGER.info(
                "Overnight loss calibration skipped: both boilers <= 20°C (Gas: %s, Elec: %s)",
                gas_temp, elec_temp,
            )
            return

        now_iso = dt_util.now().isoformat()

        def _init_boiler_state(temp):
            if temp is None:
                return None
            # Верхняя граница текущего брэкета — ближайший кратный 5 выше T
            import math
            bracket_top = math.ceil(temp / 5.0) * 5.0
            # Если temp точно на границе — брэкет начинается с неё
            if temp == bracket_top:
                bracket_top = temp
            return {
                "bracket_top":       bracket_top,
                "bracket_entered_at": now_iso,
                "prev_temp":         temp,
                "discarded":         False,  # флаг: текущий брэкет заблокирован шумом
            }

        self._overnight_state = {
            "gas":  _init_boiler_state(gas_temp)  if gas_active  else None,
            "elec": _init_boiler_state(elec_temp) if elec_active else None,
        }
        self._overnight_pending = {"gas": {}, "elec": {}}

        calibration_data = {
            "phase":       "overnight_loss",
            "gas_t_start": gas_temp  if gas_active  else None,
            "elec_t_start": elec_temp if elec_active else None,
            "started_at":  now_iso,
        }
        self.calibration_sensor.set_calibration_state("overnight_loss", calibration_data)
        _LOGGER.info(
            "Overnight Newton LUT calibration started. Gas: %s°C, Elec: %s°C",
            gas_temp, elec_temp,
        )

    async def _async_overnight_poll(self, _now=None):
        """Ежеминутный polling (01:00–05:00): обнаружение пересечений брэкетов."""
        if not self.calibration_sensor:
            return
        if self.calibration_sensor.native_value != "overnight_loss":
            return  # Быстрый выход вне ночного окна — не блокируем event loop

        now = dt_util.now()
        standby = self.calibration_sensor.get_standby_losses()

        readings = {
            "gas":  self._get_gas_temp(),
            "elec": self._get_elec_temp(),
        }

        for boiler, t_curr in readings.items():
            state = self._overnight_state.get(boiler)
            if state is None or t_curr is None:
                continue

            t_prev = state["prev_temp"]

            # --- Noise Filter: внезапный рост или падение > 2°C за 1 минуту = водозабор ---
            if abs(t_curr - t_prev) > 2.0:
                _LOGGER.warning(
                    "[overnight/%s] Water usage detected (%.1f→%.1f°C). Bracket discarded.",
                    boiler, t_prev, t_curr,
                )
                # Сбрасываем текущий брэкет и начинаем заново с текущей T
                import math
                bracket_top = math.ceil(t_curr / 5.0) * 5.0
                state["bracket_top"]        = bracket_top
                state["bracket_entered_at"] = now.isoformat()
                state["prev_temp"]          = t_curr
                state["discarded"]          = False
                continue

            bracket_top    = state["bracket_top"]
            bracket_bottom = bracket_top - 5.0

            # --- Пересечение нижней границы брэкета ---
            if t_curr <= bracket_bottom and bracket_bottom >= 20.0:
                entered_at = dt_util.parse_datetime(state["bracket_entered_at"])
                if entered_at:
                    elapsed_h = (now - entered_at).total_seconds() / 3600.0
                else:
                    elapsed_h = 0.0

                if elapsed_h >= 0.1 and not state["discarded"]:  # >= 6 минут в брэкете
                    rate = round(5.0 / elapsed_h, 4)  # °C/h
                    key  = self._get_bracket_key(bracket_top, bracket_bottom)
                    old_rate = standby.get(boiler, {}).get(key, 0.0)
                    new_rate = self._apply_ema(old_rate, rate)
                    self._overnight_pending[boiler][key] = new_rate
                    _LOGGER.info(
                        "[overnight/%s] Bracket %s completed: %.4f °C/h (EMA from %.4f). elapsed=%.2fh",
                        boiler, key, new_rate, old_rate, elapsed_h,
                    )
                elif state["discarded"]:
                    _LOGGER.debug("[overnight/%s] Bracket %s skipped (discarded by noise filter).", boiler, key)
                else:
                    _LOGGER.debug("[overnight/%s] Bracket %s too short (%.1f min), skipped.", boiler, key, elapsed_h * 60)

                # Переход в следующий брэкет вниз
                state["bracket_top"]        = bracket_bottom
                state["bracket_entered_at"] = now.isoformat()
                state["discarded"]          = False

            state["prev_temp"] = t_curr

        # Публикуем промежуточное состояние в calibration_data для UI
        cal_data = self.calibration_sensor._calibration_data or {}
        cal_data["pending_brackets_gas"]  = len(self._overnight_pending.get("gas", {}))
        cal_data["pending_brackets_elec"] = len(self._overnight_pending.get("elec", {}))
        cal_data["gas_current_temp"]  = readings["gas"]
        cal_data["elec_current_temp"] = readings["elec"]
        self.calibration_sensor.set_calibration_state("overnight_loss", cal_data)

    async def _async_overnight_end(self, _now=None):
        """Завершение ночного мониторинга в 05:00 — финализация LUT."""
        if not self.calibration_sensor or self.calibration_sensor.native_value != "overnight_loss":
            return

        now     = dt_util.now()
        standby = self.calibration_sensor.get_standby_losses()

        # --- Финализировать незавершённые брэкеты (если >= 30 мин в них) ---
        for boiler, state in self._overnight_state.items():
            if state is None or state.get("discarded"):
                continue
            entered_at = dt_util.parse_datetime(state.get("bracket_entered_at", ""))
            if not entered_at:
                continue
            elapsed_h = (now - entered_at).total_seconds() / 3600.0
            if elapsed_h >= 0.5:  # >= 30 минут — достаточно для надёжного замера
                bracket_top    = state["bracket_top"]
                bracket_bottom = bracket_top - 5.0
                if bracket_bottom >= 20.0:
                    t_curr = self._get_gas_temp() if boiler == "gas" else self._get_elec_temp()
                    if t_curr is not None:
                        # Реальное падение за elapsed_h (частичный брэкет)
                        t_prev_in_bracket = bracket_top  # вошли с верхней границы
                        actual_drop = max(0.0, t_prev_in_bracket - t_curr)
                        if actual_drop > 0.2:
                            rate = round(actual_drop / elapsed_h, 4)
                            key  = self._get_bracket_key(bracket_top, bracket_bottom)
                            old_rate = standby.get(boiler, {}).get(key, 0.0)
                            new_rate = self._apply_ema(old_rate, rate)
                            self._overnight_pending[boiler][key] = new_rate
                            _LOGGER.info(
                                "[overnight/%s] Partial bracket %s finalized: %.4f °C/h (drop=%.2f°C, elapsed=%.2fh)",
                                boiler, key, new_rate, actual_drop, elapsed_h,
                            )

        # --- Записываем все накопленные обновления LUT ---
        date_str    = now.date().isoformat()
        update_data = {"last_calibrated": date_str}

        for boiler in ("gas", "elec"):
            pending = self._overnight_pending.get(boiler, {})
            if pending:
                update_data[boiler] = pending
                _LOGGER.info(
                    "[overnight/%s] Saving %d bracket(s) to LUT: %s",
                    boiler, len(pending), list(pending.keys()),
                )

        self.calibration_sensor.update_calibration_coefficient("overnight_loss", update_data)
        self.calibration_sensor.set_calibration_state("idle", {})

        # Сброс state machine
        self._overnight_state   = {}
        self._overnight_pending = {}
        _LOGGER.info("Overnight Newton LUT calibration completed and saved.")

    # =========================================================================
    # Manually Triggered Heating Calibration Phases
    # =========================================================================
    async def async_start_calibration(
        self, 
        phase: str, 
        heating_duration_minutes: int | None = None,
        target_temperature_delta: float | None = None,
        stabilization_minutes: int | None = None
    ) -> bool:
        """Запуск ручной фазы калибровки с валидацией конфигурации и выводом ошибок в UI."""
        if not self.calibration_sensor:
            raise HomeAssistantError("Cannot start calibration: calibration sensor not registered yet.")
            
        if self.calibration_sensor.native_value != "idle":
            raise HomeAssistantError(
                f"Calibration is already running! Current active phase: {self.calibration_sensor.native_value}"
            )

        # Валидация фазы
        valid_phases = ["gas_only", "gas_with_pump", "elec_only", "elec_with_pump"]
        if phase not in valid_phases:
            raise HomeAssistantError(f"Invalid calibration phase requested: {phase}")

        # Проверка физической конфигурации сущностей перед запуском (SRE-контроль)
        if "gas" in phase:
            if not self.gas_climate:
                raise HomeAssistantError(
                    "Gas boiler climate entity (gas_boiler_climate) is not configured in settings."
                )
            if not self.gas_meter:
                raise HomeAssistantError(
                    "Gas meter sensor entity (gas_boiler_meter) is not configured in settings."
                )
            if "pump" in phase and not self.elec_temp:
                raise HomeAssistantError(
                    "Electric temperature sensor entity (elec_boiler_temp) is required for gas_with_pump phase."
                )
        else:  # elec
            if not self.elec_heater:
                raise HomeAssistantError(
                    "Electric heater switch entity (elec_boiler_heater) is not configured in settings."
                )
            if not self.elec_energy:
                raise HomeAssistantError(
                    "Electric energy sensor entity (elec_boiler_energy) is not configured in settings."
                )
            if not self.elec_temp:
                raise HomeAssistantError(
                    "Electric temperature sensor entity (elec_boiler_temp) is not configured in settings."
                )
            if "pump" in phase and not self.gas_climate:
                raise HomeAssistantError(
                    "Gas boiler climate entity (gas_boiler_climate) is required for elec_with_pump phase."
                )

        # Проверка и сбор показаний на старте
        try:
            baseline = self._get_baseline_readings(phase)
        except ValueError as err:
            _LOGGER.error("Calibration baseline readings failed: %s", err)
            raise HomeAssistantError(f"Calibration baseline readings failed: {err}")

        # Сохраняем временное состояние для устойчивости к перезапуску
        cal_data = {
            "phase": phase,
            "t_start": baseline["t_start"],
            "v_start": baseline.get("v_start"),
            "e_start": baseline.get("e_start"),
            "heating_duration_minutes": heating_duration_minutes,
            "target_temperature_delta": target_temperature_delta,
            "stabilization_minutes": stabilization_minutes,
            "started_at": dt_util.now().isoformat()
        }
        self.calibration_sensor.set_calibration_state(phase, cal_data)

        # Запуск фонового процесса выполнения калибровки
        self.hass.async_create_task(self._async_execute_heating_phase(phase, cal_data))
        return True

    async def _async_execute_heating_phase(self, phase: str, cal_data: dict):
        """Фоновый процесс циклического нагрева, стабилизации и финализации."""
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

        await self._async_stabilize_and_finalize(phase, cal_data, stab_duration)

    async def _async_stabilize_and_finalize(self, phase: str, cal_data: dict, wait_seconds: float):
        """Ожидание стабилизации температуры и финальный расчет коэффициентов."""
        if wait_seconds > 0:
            elapsed = 0
            total_wait = wait_seconds
            original_duration = float(cal_data.get("stabilization_duration", 600.0))
            stab_minutes = int(original_duration // 60)
            
            while elapsed < total_wait:
                cal_data["status_desc"] = f"Стабилизация {stab_minutes} мин"
                cal_data["time_left"] = int(max(0, total_wait - elapsed))
                self.calibration_sensor.set_calibration_state(phase, cal_data)
                
                step = min(10.0, total_wait - elapsed)
                await asyncio.sleep(step)
                elapsed += step
            
        _LOGGER.info("Stabilization delay completed. Performing mathematical final calculations...")

        # Получаем конечные показания датчиков
        t_start = cal_data["t_start"]
        if "pump" in phase:
            t_end = self._get_system_temp()
        elif "gas" in phase:
            t_end = self._get_gas_temp()
        else:
            t_end = self._get_elec_temp()
        
        if t_end is None:
            _LOGGER.error("Calibration final calculation failed: unable to read final stabilized temperature.")
            self.calibration_sensor.set_calibration_state("idle", {})
            return
            
        date_str = dt_util.now().date().isoformat()
        update_data = {"last_calibrated": date_str}
        
        if "gas" in phase:
            v_start = cal_data["v_start"]
            v_end = self._get_gas_meter()
            if v_end is None or v_start is None:
                _LOGGER.error("Gas calibration final calculations failed: gas meter state is unavailable.")
                self.calibration_sensor.set_calibration_state("idle", {})
                return
                
            delta_v = v_end - v_start
            if delta_v <= 0.001:
                _LOGGER.error("Gas calibration failed: delta gas meter too small (%s m³). Div by zero safety override triggered.", delta_v)
                self.calibration_sensor.set_calibration_state("idle", {})
                return
                
            efficiency = round((t_end - t_start) / delta_v, 4)
            update_data["efficiency_c_per_m3"] = efficiency
            _LOGGER.info("Gas Efficiency calculated successfully: %s °C/m³", efficiency)
            
        else:  # elec
            e_start = cal_data["e_start"]
            e_end = self._get_elec_energy()
            if e_end is None or e_start is None:
                _LOGGER.error("Electric calibration final calculations failed: energy meter state is unavailable.")
                self.calibration_sensor.set_calibration_state("idle", {})
                return
                
            delta_e = e_end - e_start
            if delta_e <= 0.001:
                _LOGGER.error("Electric calibration failed: delta energy too small (%s kWh). Div by zero safety override triggered.", delta_e)
                self.calibration_sensor.set_calibration_state("idle", {})
                return
                
            efficiency = round((t_end - t_start) / delta_e, 4)
            update_data["efficiency_c_per_kwh"] = efficiency
            _LOGGER.info("Electric Efficiency calculated successfully: %s °C/kWh", efficiency)

            # Calculate and save heater_power_kw based on readings, duration, or fallback
            readings = cal_data.get("power_readings", [])
            avg_p = None
            if readings:
                avg_p = sum(readings) / len(readings)
                _LOGGER.info("Average electric power from instant sensor: %.3f kW", avg_p)
            
            if avg_p is None:
                started_at_str = cal_data.get("started_at")
                ended_at_str = cal_data.get("heating_ended_at")
                if started_at_str and ended_at_str:
                    try:
                        started_dt = dt_util.parse_datetime(started_at_str)
                        ended_dt = dt_util.parse_datetime(ended_at_str)
                        if started_dt and ended_dt:
                            duration_hours = (ended_dt - started_dt).total_seconds() / 3600.0
                            if duration_hours > 0.0:
                                avg_p = delta_e / duration_hours
                                _LOGGER.info("Calculated electric power from energy/duration: %.3f kW", avg_p)
                    except Exception as ex:
                        _LOGGER.warning("Could not calculate electric power from duration: %s", ex)
            
            if avg_p is None or avg_p <= 0.0:
                avg_p = 2.5
                _LOGGER.info("Using default electric power: %.3f kW", avg_p)
            
            update_data["heater_power_kw"] = round(avg_p, 3)

        # Сохранение результатов и сброс в IDLE
        self.calibration_sensor.update_calibration_coefficient(phase, update_data)
        self.calibration_sensor.set_calibration_state("idle", {})
        _LOGGER.info("Calibration phase %s completed successfully.", phase)

    async def async_recover_calibration(self, calibration_data: dict):
        """Восстановление прерванной калибровки после перезапуска Home Assistant."""
        phase = calibration_data.get("phase")
        if not phase:
            return
            
        _LOGGER.info("Attempting to recover active calibration phase: %s", phase)
        
        # Если HA перезагрузился на этапе ожидания стабилизации
        if "heating_ended_at" in calibration_data:
            ended_dt = dt_util.parse_datetime(calibration_data["heating_ended_at"])
            if ended_dt:
                elapsed = (dt_util.now() - ended_dt).total_seconds()
                stab_total = float(calibration_data.get("stabilization_duration", 600.0))
                remaining = stab_total - elapsed
                if remaining > 0:
                    _LOGGER.info("Resuming stabilization delay for phase %s: %s seconds remaining.", phase, round(remaining, 1))
                    self.hass.async_create_task(self._async_stabilize_and_finalize(phase, calibration_data, remaining))
                    return
                else:
                    _LOGGER.info("Stabilization delay elapsed during reboot. final calculations will run immediately.")
                    self.hass.async_create_task(self._async_stabilize_and_finalize(phase, calibration_data, 0.0))
                    return
                    
        # Если HA перезапустился прямо в процессе нагрева - аварийно выключаем нагреватели ради безопасности
        _LOGGER.warning("Home Assistant restarted during heating phase of %s. Emergency safety cooldown triggered.", phase)
        await self._actuate_heating(phase, turn_on=False)
        self.calibration_sensor.set_calibration_state("idle", {})

    # =========================================================================
    # Helpers: Readings and Actuation
    # =========================================================================
    def _get_elec_power(self) -> float | None:
        """Получить показания мгновенной мощности ТЭНа."""
        if not self.elec_power:
            return None
        state = self.hass.states.get(self.elec_power)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            val = float(state.state)
            if val > 15.0:
                val /= 1000.0
            return val
        except (ValueError, TypeError):
            return None

    def _get_baseline_readings(self, phase: str) -> dict:
        """Сбор базовых параметров перед калибровкой с жесткой проверкой ошибок."""
        readings = {}
        
        # 1. Температура
        if "pump" in phase:
            t = self._get_system_temp()
            if t is None:
                raise ValueError("System temperature is unavailable (check gas and electric temperature sensors).")
            readings["t_start"] = t
        elif "gas" in phase:
            t = self._get_gas_temp()
            if t is None:
                raise ValueError("Gas climate temperature is unavailable.")
            readings["t_start"] = t
        else:
            t = self._get_elec_temp()
            if t is None:
                raise ValueError("Electric temperature sensor is unavailable.")
            readings["t_start"] = t
            
        # 2. Счетчики ресурсов
        if "gas" in phase:
            v = self._get_gas_meter()
            if v is None:
                raise ValueError("Gas meter sensor is not configured or unavailable.")
            readings["v_start"] = v
        else:
            e = self._get_elec_energy()
            if e is None:
                raise ValueError("Electric energy sensor is not configured or unavailable.")
            readings["e_start"] = e
            
        return readings

    async def _actuate_heating(self, phase: str, turn_on: bool, target_temp: float = None):
        """Включение или выключение исполнительных механизмов в зависимости от фазы."""
        if "gas" in phase:
            if not self.gas_climate:
                return
                
            if turn_on and target_temp is not None:
                # 1. Включаем нагрев газового бойлера
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    "set_hvac_mode",
                    {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": "heat"}
                )
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    "set_temperature",
                    {ATTR_ENTITY_ID: self.gas_climate, ATTR_TEMPERATURE: target_temp}
                )
                
                # 2. Если фаза с насосом - заводим циркуляцию
                if "pump" in phase and self.pump:
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        SERVICE_TURN_ON,
                        {ATTR_ENTITY_ID: self.pump}
                    )
            else:
                # Гасим нагрев
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
        else:  # elec
            if not self.elec_heater:
                return
                
            if turn_on:
                # 1. Открываем байпасный клапан (В последовательный режим)
                if self.bypass_valve:
                    domain = self.bypass_valve.split(".")[0]
                    await self.hass.services.async_call(
                        domain,
                        SERVICE_TURN_ON,
                        {ATTR_ENTITY_ID: self.bypass_valve}
                    )
                # 2. Включаем ТЭН
                await self.hass.services.async_call(
                    SWITCH_DOMAIN,
                    SERVICE_TURN_ON,
                    {ATTR_ENTITY_ID: self.elec_heater}
                )
                # 3. Если фаза с насосом - заводим циркуляцию
                if "pump" in phase and self.pump:
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        SERVICE_TURN_ON,
                        {ATTR_ENTITY_ID: self.pump}
                    )
            else:
                # Выключаем ТЭН
                await self.hass.services.async_call(
                    SWITCH_DOMAIN,
                    SERVICE_TURN_OFF,
                    {ATTR_ENTITY_ID: self.elec_heater}
                )
                if self.pump:
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        SERVICE_TURN_OFF,
                        {ATTR_ENTITY_ID: self.pump}
                    )
                # Примечание: байпасный клапан оставляем в покое (не перекрываем насильно)

    # Вспомогательные геттеры физических сенсоров
    def _get_system_temp(self) -> float | None:
        """Расчет средневзвешенной температуры системы на основе емкостей нагревателей."""
        t_gas = self._get_gas_temp()
        t_elec = self._get_elec_temp()
        if t_gas is None or t_elec is None:
            return None
        
        try:
            vol_gas = float(self.gas_capacity)
            vol_elec = float(self.elec_capacity)
        except (ValueError, TypeError):
            vol_gas = 100.0
            vol_elec = 100.0
            
        total_vol = vol_gas + vol_elec
        if total_vol <= 0:
            return (t_gas + t_elec) / 2.0
            
        return round((t_gas * vol_gas + t_elec * vol_elec) / total_vol, 4)

    def _get_gas_temp(self) -> float | None:
        if not self.gas_climate:
            return None
        state = self.hass.states.get(self.gas_climate)
        if state and state.attributes.get("current_temperature") is not None:
            try:
                return float(state.attributes.get("current_temperature"))
            except (ValueError, TypeError):
                pass
        return None

    def _get_elec_temp(self) -> float | None:
        if not self.elec_temp:
            return None
        state = self.hass.states.get(self.elec_temp)
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    def _get_gas_meter(self) -> float | None:
        if not self.gas_meter:
            return None
        state = self.hass.states.get(self.gas_meter)
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    def _get_elec_energy(self) -> float | None:
        if not self.elec_energy:
            return None
        state = self.hass.states.get(self.elec_energy)
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    # =========================================================================
    # LUT Helpers — Newton's Law of Cooling bracket utilities
    # =========================================================================

    @staticmethod
    def _get_bracket_key(bracket_top: float, bracket_bottom: float) -> str:
        """Формирует строковый ключ брэкета: '70_65' для [70°C → 65°C]."""
        return f"{int(bracket_top)}_{int(bracket_bottom)}"

    @staticmethod
    def _apply_ema(old: float, new_measurement: float, alpha: float = 0.3) -> float:
        """EMA с cold-start: если старое значение 0.0 — принимаем новое напрямую.
        
        EMA = alpha * new + (1 - alpha) * old
        Cold-start: если old == 0.0 — возвращаем new_measurement без занижения.
        """
        if old == 0.0:
            return round(new_measurement, 4)
        return round(alpha * new_measurement + (1.0 - alpha) * old, 4)

    @staticmethod
    def _get_lut_rate(lut: dict, temp: float) -> float | None:
        """Находит скорость потерь (°C/h) для текущей температуры по LUT.
        
        Брэкеты: 75_70, 70_65, 65_60 ... 25_20
        Для T=63°C → брэкет '65_60'.
        Для T ниже нижней границы LUT → берём ближайший нижний брэкет.
        """
        if not lut:
            return None

        # Определяем нижнюю границу брэкета для данной T
        import math
        bracket_top    = math.ceil(temp / 5.0) * 5.0
        bracket_bottom = bracket_top - 5.0

        # Если T точно на верхней границе — принадлежит брэкету выше
        if temp == bracket_top:
            bracket_top    += 5.0
            bracket_bottom  = bracket_top - 5.0

        key = f"{int(bracket_top)}_{int(bracket_bottom)}"
        rate = lut.get(key)
        if rate is not None and rate > 0:
            return float(rate)

        # Fallback: ищем ближайший существующий брэкет с ненулевым значением
        # (бойлер мог быть холоднее чем диапазон LUT)
        best_key   = None
        best_delta = float("inf")
        for k, v in lut.items():
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            try:
                parts = k.split("_")
                k_top = int(parts[0])
                k_bot = int(parts[1])
                k_mid = (k_top + k_bot) / 2.0
                delta = abs(temp - k_mid)
                if delta < best_delta:
                    best_delta = delta
                    best_key   = k
            except (ValueError, IndexError):
                continue

        if best_key:
            return float(lut[best_key])
        return None

    async def _async_dp_plan_changed(self, event):
        """Handle DP plan state changes."""
        await self._async_apply_current_dp_plan()

    async def _async_system_mode_changed(self, event):
        """Handle system mode selection changes (Auto/Manual)."""
        new_state = event.data.get("new_state")
        if new_state:
            self.current_mode = new_state.state
            await self._async_apply_current_dp_plan()

    async def _async_apply_current_dp_plan(self):
        """Apply current DP plan recommended actions to physical hardware if in Auto mode."""
        if self.current_mode.lower() != "auto":
            return

        dp_state = self.hass.states.get("sensor.boiler_dp")
        if not dp_state or dp_state.state in ("unknown", "unavailable", "error", "idle_bypass", "NO PATH", "NO CALIB DATA"):
            # Safe default to IDLE mode, closed bypass (OFF)
            await self._async_set_boiler_mode("IDLE", "OFF")
            return

        mode = dp_state.state.upper()
        recommended_bypass = dp_state.attributes.get("recommended_bypass", "OFF").upper()
        await self._async_set_boiler_mode(mode, recommended_bypass)

    async def _async_set_boiler_mode(self, mode: str, recommended_bypass: str):
        """Turn on/off actuators based on mode and recommended bypass."""
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
                        )
        except Exception as ex:
            _LOGGER.error("Error applying DP mode %s: %s", mode, ex)
        finally:
            self._is_applying_dp_plan = False
