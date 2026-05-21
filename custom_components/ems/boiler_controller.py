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
        
        # Sensor reference (will be registered by sensor.py)
        self.calibration_sensor = None
        
        # Mode property, will be updated by EmsBoilerModeSelect
        self.current_mode = "Auto"
        
        # Capacity and cost settings
        self.gas_capacity = config.get("gas_boiler_capacity", 100)
        self.elec_capacity = config.get("elec_boiler_capacity", 100)
        self.gas_cost_m3 = config.get("gas_cost_m3", 0.0)
        
    async def async_setup(self):
        """Регистрация безопасных слушателей событий и таймеров калибровки."""
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
        
        # 3. Throttling вычислений стоимости (раз в 2 минуты)
        async_track_time_interval(
            self.hass,
            self._async_calculate_costs,
            datetime.timedelta(minutes=2)
        )
        
        # 4. Регистрация автоматического ночного теста охлаждения (01:00 - 05:00)
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
        valve OFF = электробойлер ИЗОЛИРОВАН → немедленно гасим ТЭН и насос.
        """
        valve_state = self.hass.states.get(self.bypass_valve)

        if valve_state and valve_state.state == STATE_OFF:
            if self.elec_heater:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.elec_heater}
                )
            if self.pump:
                await self.hass.services.async_call(
                    SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.pump}
                )
            
    async def _async_override_check(self, event):
        """Отклоняет ручные изменения в режиме Auto или если клапан перекрыт."""
        # Пропускаем любые проверки, если сейчас выполняется калибровка
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
                # Откат в предыдущее состояние
                service = "turn_on" if old_state.state == STATE_ON else "turn_off"
                await self.hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id})

        # Блокировка 2: Попытка включить насос/ТЭН при изолированном электробойлере (valve OFF)
        elif valve_state and valve_state.state == STATE_OFF and new_state and new_state.state == STATE_ON:
            await self.hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})
                
    async def _async_calculate_costs(self, _now):
        """Расчет стоимости и эффективности. Не спамит Event Loop/БД."""
        pass # Реализация расчетов стоимости

    # =========================================================================
    # Passive Overnight Thermal Loss Calibration (01:00 AM - 05:00 AM)
    # =========================================================================
    async def _async_overnight_start(self, _now=None):
        """Запуск пассивного теста остывания в 01:00."""
        if not self.calibration_sensor:
            return
            
        if self.calibration_sensor.native_value != "idle":
            _LOGGER.warning("Overnight loss calibration skipped: calibration already running (%s)", self.calibration_sensor.native_value)
            return

        # Получаем температуры газового и электрического бойлеров
        gas_temp = self._get_gas_temp()
        elec_temp = self._get_elec_temp()
        
        gas_active = gas_temp is not None and gas_temp > 40.0
        elec_active = elec_temp is not None and elec_temp > 40.0
        
        if not gas_active and not elec_active:
            _LOGGER.info("Overnight loss calibration skipped: all boiler temperatures <= 40°C (Gas: %s, Elec: %s)", gas_temp, elec_temp)
            return
            
        calibration_data = {
            "phase": "overnight_loss",
            "gas_t_start": gas_temp if gas_active else None,
            "elec_t_start": elec_temp if elec_active else None,
            "started_at": dt_util.now().isoformat()
        }
        
        self.calibration_sensor.set_calibration_state("overnight_loss", calibration_data)
        _LOGGER.info("Overnight thermal loss calibration started (Gas: %s, Elec: %s)", gas_temp, elec_temp)

    async def _async_overnight_end(self, _now=None):
        """Завершение пассивного теста остывания в 05:00."""
        if not self.calibration_sensor or self.calibration_sensor.native_value != "overnight_loss":
            return
            
        cal_data = self.calibration_sensor._calibration_data
        if not cal_data:
            self.calibration_sensor.set_calibration_state("idle")
            return
            
        gas_t_start = cal_data.get("gas_t_start")
        elec_t_start = cal_data.get("elec_t_start")
        
        gas_temp_end = self._get_gas_temp()
        elec_temp_end = self._get_elec_temp()
        
        update_data = {"last_calibrated": dt_util.now().date().isoformat()}
        
        if gas_t_start is not None and gas_temp_end is not None:
            u_loss_gas = max(0.0, (gas_t_start - gas_temp_end) / 4.0)
            update_data["gas_hourly_loss_c"] = round(u_loss_gas, 4)
            _LOGGER.info("Calculated gas hourly standby loss: %s °C/h", u_loss_gas)
            
        if elec_t_start is not None and elec_temp_end is not None:
            u_loss_elec = max(0.0, (elec_t_start - elec_temp_end) / 4.0)
            update_data["elec_hourly_loss_c"] = round(u_loss_elec, 4)
            _LOGGER.info("Calculated electric hourly standby loss: %s °C/h", u_loss_elec)
            
        self.calibration_sensor.update_calibration_coefficient("overnight_loss", update_data)
        self.calibration_sensor.set_calibration_state("idle")
        _LOGGER.info("Overnight thermal loss calibration completed successfully.")

    # =========================================================================
    # Manually Triggered Heating Calibration Phases
    # =========================================================================
    async def async_start_calibration(self, phase: str) -> bool:
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
            # Фазы с насосом: нагрев в течение ровно 5 минут (300 секунд)
            _LOGGER.info("Starting active calibration phase: %s. Baseline Temp: %s°C, Duration: 300s", phase, t_start)
            await self._actuate_heating(phase, turn_on=True, target_temp=80.0)
            
            duration = 300
            elapsed = 0
            success = True
            try:
                while elapsed < duration:
                    await asyncio.sleep(10)
                    elapsed += 10
                    t_curr = self._get_system_temp()
                    _LOGGER.info("[%s] Heating in progress... Elapsed: %ss/%ss, Current Temp: %s°C", phase, elapsed, duration, t_curr)
            except Exception as ex:
                _LOGGER.error("Error during calibration heating loop: %s", ex)
                success = False
        else:
            # Одиночные фазы без насоса: нагрев до достижения T_start + 12.0°C (макс. 90 минут)
            t_target = t_start + 12.0
            _LOGGER.info("Starting active calibration phase: %s. Baseline Temp: %s°C, Target Temp: %s°C", phase, t_start, t_target)
            await self._actuate_heating(phase, turn_on=True, target_temp=t_target)
            
            timeout = 5400  # 90 минут в секундах
            elapsed = 0
            try:
                while elapsed < timeout:
                    await asyncio.sleep(10)
                    elapsed += 10
                    
                    if "gas" in phase:
                        t_curr = self._get_gas_temp()
                    else:
                        t_curr = self._get_elec_temp()
                        
                    _LOGGER.info("[%s] Heating in progress... Elapsed: %ss/%ss, Current Temp: %s°C, Target Temp: %s°C", phase, elapsed, timeout, t_curr, t_target)
                    
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
            self.calibration_sensor.set_calibration_state("idle")
            return

        # 4. Запуск 3-минутного периода стабилизации температуры (тепловая инерция)
        _LOGGER.info("Heating target reached. Starting 3-minute stabilization delay...")
        cal_data["heating_ended_at"] = dt_util.now().isoformat()
        self.calibration_sensor.set_calibration_state(phase, cal_data)
        
        await self._async_stabilize_and_finalize(phase, cal_data, 180.0)

    async def _async_stabilize_and_finalize(self, phase: str, cal_data: dict, wait_seconds: float):
        """Ожидание стабилизации температуры и финальный расчет коэффициентов."""
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            
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
            self.calibration_sensor.set_calibration_state("idle")
            return
            
        date_str = dt_util.now().date().isoformat()
        update_data = {"last_calibrated": date_str}
        
        if "gas" in phase:
            v_start = cal_data["v_start"]
            v_end = self._get_gas_meter()
            if v_end is None or v_start is None:
                _LOGGER.error("Gas calibration final calculations failed: gas meter state is unavailable.")
                self.calibration_sensor.set_calibration_state("idle")
                return
                
            delta_v = v_end - v_start
            if delta_v <= 0.001:
                _LOGGER.error("Gas calibration failed: delta gas meter too small (%s m³). Div by zero safety override triggered.", delta_v)
                self.calibration_sensor.set_calibration_state("idle")
                return
                
            efficiency = round((t_end - t_start) / delta_v, 4)
            update_data["efficiency_c_per_m3"] = efficiency
            _LOGGER.info("Gas Efficiency calculated successfully: %s °C/m³", efficiency)
            
        else:  # elec
            e_start = cal_data["e_start"]
            e_end = self._get_elec_energy()
            if e_end is None or e_start is None:
                _LOGGER.error("Electric calibration final calculations failed: energy meter state is unavailable.")
                self.calibration_sensor.set_calibration_state("idle")
                return
                
            delta_e = e_end - e_start
            if delta_e <= 0.001:
                _LOGGER.error("Electric calibration failed: delta energy too small (%s kWh). Div by zero safety override triggered.", delta_e)
                self.calibration_sensor.set_calibration_state("idle")
                return
                
            efficiency = round((t_end - t_start) / delta_e, 4)
            update_data["efficiency_c_per_kwh"] = efficiency
            _LOGGER.info("Electric Efficiency calculated successfully: %s °C/kWh", efficiency)

        # Сохранение результатов и сброс в IDLE
        self.calibration_sensor.update_calibration_coefficient(phase, update_data)
        self.calibration_sensor.set_calibration_state("idle")
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
                remaining = 180.0 - elapsed
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
        self.calibration_sensor.set_calibration_state("idle")

    # =========================================================================
    # Helpers: Readings and Actuation
    # =========================================================================
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
