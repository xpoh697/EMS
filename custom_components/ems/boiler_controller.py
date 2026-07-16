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
from .const import CONF_CALIBRATION_TYPE, CONF_WATER_FLOW_SENSOR, CONF_PEOPLE_HOME_SENSOR, CONF_BOILER_WARM_DIFF, DEFAULT_BOILER_WARM_DIFF

_LOGGER = logging.getLogger(__name__)

class BoilerController:
    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self.entry_id = None
        
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
        self._calibration_task = None
        
        # Mode property, will be updated by EmsBoilerModeSelect
        self.current_mode = "Auto"
        
        # Флаги программной отсечки нагрева и перекачки тепла
        self._elec_cutoff_active = False
        self._gas_cutoff_active = False
        self._gas_heating_delayed = False
        self._t_max_elec = None
        self._t_max_gas = None
        
        # Флаги ручного цикла нагрева
        self._manual_heating_active = False
        self._manual_heating_mode = "GAS"
        self._manual_heating_setpoint = 50.0
        self._manual_pump_dump_active = False
        self._warm_boiler_bypass_active = False
        
        # Capacity and cost settings
        self.gas_capacity = config.get("gas_boiler_capacity", 100)
        self.elec_capacity = config.get("elec_boiler_capacity", 100)
        self.gas_cost_m3 = config.get("gas_cost_m3", 0.0)
        self.people_home_sensor = config.get(CONF_PEOPLE_HOME_SENSOR)
        
        # EMA solar deficit variables to protect home battery
        self._solar_deficit_cutoff = False
        self._avg_pv = None
        self._avg_load = None

    @property
    def storage(self):
        if not hasattr(self, "entry_id") or not self.entry_id:
            return None
        from .const import DOMAIN
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.entry_id)
        return entry_data.get("storage") if entry_data else None
        
    async def async_setup(self):
        """Регистрация безопасных слушателей событий и таймеров калибровки."""
        self._is_applying_dp_plan = False

        # 2. Защита от ручного включения в режиме Auto (только для электронагревателя)
        if self.elec_heater:
            async_track_state_change_event(
                self.hass,
                [self.elec_heater],
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

        # 4a. Следим за изменением сенсора присутствия
        if self.people_home_sensor:
            async_track_state_change_event(
                self.hass,
                [self.people_home_sensor],
                self._async_people_home_changed
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
        
        # 6. Регистрация периодического опроса дефицита солнца (каждые 10 секунд)
        async_track_time_interval(
            self.hass,
            self._async_solar_deficit_monitor,
            datetime.timedelta(seconds=10)
        )
        
    def _update_cutoff_states(self):
        """Обновление флагов отсечки нагрева и перекачки с учетом гистерезиса и режима Fail-Safe."""
        # Получаем плановые целевые температуры из расписания DP для текущего часа
        dp_t_max_elec = None
        dp_t_max_gas = None
        dp_state = self.hass.states.get("sensor.boiler_dp")
        if dp_state and self.current_mode.lower() == "auto":
            schedule = dp_state.attributes.get("schedule", [])
            if schedule:
                dp_t_max_elec = schedule[0].get("temp_elec_end")
                dp_t_max_gas = schedule[0].get("temp_gas_end")

        # 1. Электробойлер
        t_elec = self._get_elec_temp()
        t_max_elec = dp_t_max_elec
        storage = self.storage
        if t_max_elec is None:
            t_max_elec = float(self.config.get("elec_boiler_max_temp", 70.0))
            if storage and self.current_mode.lower() == "auto":
                t_max_elec = min(t_max_elec, float(getattr(storage, "boiler_auto_temp_limit", 60.0)))
        self._t_max_elec = t_max_elec
        hysteresis = 5.0
        
        if t_elec is None:
            mode = dp_state.state.upper() if dp_state else "IDLE"
            if "ELEC" in mode and self.current_mode.lower() == "auto":
                if not self._elec_cutoff_active:
                    _LOGGER.warning("EMS Boiler Controller: Temperature sensor for electric boiler is unavailable during active heating. Activating safety cutoff.")
                self._elec_cutoff_active = True
        else:
            # Общая остановка при достижении setpoint или дефиците солнца
            if t_elec >= t_max_elec:
                if not self._elec_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler reached setpoint (%.1f >= %.1f). Stopping heating.", t_elec, t_max_elec)
                self._elec_cutoff_active = True
            elif self._solar_deficit_cutoff:
                self._elec_cutoff_active = True
            elif t_elec < t_max_elec - hysteresis:
                if self._elec_cutoff_active:
                    _LOGGER.info("EMS Boiler Controller: Electric boiler cooled below hysteresis (%.1f < %.1f). Resuming heating.", t_elec, t_max_elec - hysteresis)
                self._elec_cutoff_active = False

        # 2. Газовый котел
        t_gas = self._get_gas_temp()
        t_max_gas = dp_t_max_gas
        if t_max_gas is None:
            t_max_gas = float(self.config.get("gas_boiler_max_temp", 50.0))
            if storage and self.current_mode.lower() == "auto":
                t_max_gas = min(t_max_gas, float(getattr(storage, "boiler_auto_temp_limit", 60.0)))
        self._t_max_gas = t_max_gas
        
        if t_gas is None:
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

    async def _async_pump_only_timer(self):
        """Auto stop manual PUMP_ONLY mode after 15 minutes."""
        await asyncio.sleep(900)
        if self._manual_heating_active and self._manual_heating_mode == "PUMP_ONLY":
            _LOGGER.info("EMS Boiler Controller: Manual PUMP_ONLY timeout reached. Stopping.")
            await self.async_stop_manual_heating()

    async def _async_temp_changed(self, event):
        """Обработка изменения температуры бойлеров."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not new_state:
            return

        entity_id = event.data.get("entity_id")
        temp_changed = False

        if entity_id == self.elec_temp:
            old_val = old_state.state if old_state else None
            new_val = new_state.state
            if old_val != new_val:
                try:
                    if old_val is not None and old_val not in ("unknown", "unavailable"):
                        old_f = float(old_val)
                    else:
                        old_f = None
                    if new_val not in ("unknown", "unavailable"):
                        new_f = float(new_val)
                        if old_f != new_f:
                            temp_changed = True
                    else:
                        temp_changed = True
                except (ValueError, TypeError):
                    temp_changed = True
        elif entity_id == self.gas_climate:
            old_temp = old_state.attributes.get("current_temperature") if old_state else None
            new_temp = new_state.attributes.get("current_temperature")
            if old_temp != new_temp:
                temp_changed = True

        if not temp_changed:
            return

        if self.current_mode.lower() == "auto":
            await self._async_apply_current_dp_plan()
        elif self.current_mode.lower() == "manual" and self._manual_heating_active:
            await self._async_apply_manual_heating()
        elif self.current_mode.lower() == "manual" and not self._manual_heating_active:
            # Manual mode, no active heating cycle — still manage bypass by T_elec.
            # Without this, the bypass stays locked in its last Auto-mode state
            # (e.g., OFF from a NO PATH fallback), isolating a hot electric boiler.
            t_elec_idle = self._get_elec_temp()
            t_min_idle = float(self.config.get("thermostat_set_temp", 40.0))
            warm_diff_idle = float(self.config.get(CONF_BOILER_WARM_DIFF, DEFAULT_BOILER_WARM_DIFF))
            idle_bypass = "ON" if (t_elec_idle is not None and t_elec_idle >= (t_min_idle - warm_diff_idle)) else "OFF"
            await self._async_set_boiler_mode("IDLE", idle_bypass)

    async def async_start_manual_heating(self, mode: str, setpoint: float):
        """Запуск ручного цикла нагрева с жесткой валидацией на бэкенде."""
        if self.current_mode.lower() != "manual":
            raise HomeAssistantError("Cannot start manual heating: system mode is not Manual.")
            
        valid_modes = ["GAS", "GAS_PUMP", "ELEC", "ELEC_PUMP", "PUMP_ONLY"]
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
        
        if mode == "PUMP_ONLY":
            self.hass.async_create_task(self._async_pump_only_timer())

    async def async_stop_manual_heating(self):
        """Остановка ручного цикла нагрева и принудительное отключение нагревателей."""
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
        """Выполнение ручного цикла нагрева в зависимости от выбранного режима."""
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
                elif mode in ("GAS_PUMP", "PUMP_ONLY"):
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
            self._is_applying_dp_plan = False

    async def _async_override_check(self, event):
        """Отклоняет ручные изменения в режиме Auto."""
        # Пропускаем любые проверки, если сейчас выполняется калибровка или применяется DP-план
        if getattr(self, "_is_applying_dp_plan", False):
            return

        if self.calibration_sensor and self.calibration_sensor.native_value != "idle":
            return

        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        # Блокировка 1: Режим Auto запрещает ручное управление
        if self.current_mode.lower() == "auto":
            if new_state and old_state and new_state.state != old_state.state:
                # Определим плановое состояние устройства на основе сенсора DP
                dp_state = self.hass.states.get("sensor.boiler_dp")
                if not dp_state or dp_state.state in ("unknown", "unavailable", "error", "idle_bypass", "NO PATH", "NO CALIB DATA"):
                    expected_state = STATE_OFF
                else:
                    mode = dp_state.state.upper()
                    if entity_id == self.elec_heater:
                        if "ELEC" in mode:
                            expected_state = STATE_OFF if self._elec_cutoff_active else STATE_ON
                        else:
                            expected_state = STATE_OFF
                    else:
                        expected_state = None

                # Если новое состояние совпадает с целевым плановым — пропускаем!
                if expected_state is not None and new_state.state == expected_state:
                    return

                # Если не совпадает — откатываем к плановому (или предыдущему)
                target_state = expected_state if expected_state is not None else old_state.state
                service = "turn_on" if target_state == STATE_ON else "turn_off"
                await self.hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id})
                
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

    def _get_water_flow_rate(self) -> float:
        flow_sensor = self.config.get(CONF_WATER_FLOW_SENSOR)
        if not flow_sensor:
            return 0.0
        state = self.hass.states.get(flow_sensor)
        if not state or state.state in ("unknown", "unavailable"):
            return 0.0
        if flow_sensor.startswith("binary_sensor."):
            return 5.0 if state.state == STATE_ON else 0.0
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 0.0

    def _is_water_flowing(self) -> bool:
        return self._get_water_flow_rate() > 0.5

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
            # Дополнительная проверка мощности ТЭНа, если переключатель выключен, но мощность идет
            if self.elec_power:
                pow_state = self.hass.states.get(self.elec_power)
                if pow_state and pow_state.state not in (None, "unknown", "unavailable"):
                    try:
                        if float(pow_state.state) > 50.0:
                            return True
                    except (ValueError, TypeError):
                        pass
        if self.gas_climate:
            state = self.hass.states.get(self.gas_climate)
            if state:
                hvac_action = state.attributes.get("hvac_action")
                if hvac_action == "heating":
                    return True
                elif hvac_action is None and state.state in ("heat", "on"):
                    # Безопасный фолбэк при отсутствии hvac_action
                    t_curr = state.attributes.get("current_temperature")
                    t_target = state.attributes.get("temperature")
                    if t_curr is None or t_target is None:
                        return True
                    try:
                        if float(t_curr) < float(t_target):
                            return True
                    except (ValueError, TypeError):
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

    def _init_auto_standby_boiler_state(self, temp: float, prev_state: dict | None = None) -> dict | None:
        if temp is None:
            return None
        import math
        bracket_top = math.ceil(temp / 5.0) * 5.0
        if temp == bracket_top:
            bracket_top += 5.0
        # Preserve bracket_entered_at and start_temp if still in the same bracket —
        # frequent interruptions must not reset the accumulation timer
        if prev_state and prev_state.get("bracket_top") == bracket_top:
            return {
                "bracket_top": bracket_top,
                "bracket_entered_at": prev_state["bracket_entered_at"],
                "start_temp": prev_state["start_temp"],
                "prev_temp": temp,
            }
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
                old_rate_raw = standby.get(boiler, {}).get(key, 0.0)
                old_rate = old_rate_raw.get("value", 0.0) if isinstance(old_rate_raw, dict) else float(old_rate_raw or 0.0)
                new_rate = self._apply_ema(old_rate, rate, alpha=0.1)
                
                self.calibration_sensor.update_calibration_coefficient(
                    "overnight_loss",
                    {
                        boiler: {key: new_rate},
                        "last_calibrated": dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
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
            
        # Pass current state so bracket_entered_at is preserved when still in same bracket
        self._auto_standby_state[boiler] = self._init_auto_standby_boiler_state(t_curr, prev_state=state)

    async def _async_auto_standby_poll(self):
        if not self.calibration_sensor:
            return
            
        if self.calibration_sensor.native_value != "idle":
            self._auto_standby_state = {}
            return
            
        self._check_init_auto_standby()
        
        flow_rate = self._get_water_flow_rate()
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
            
            # Initialize flow debounce count for this boiler
            if "flow_debounce" not in state:
                state["flow_debounce"] = 0
            
            interrupted = False
            reason = ""
            
            # Water flow checks with debounce
            if flow_rate >= 2.0:
                interrupted = True
                reason = f"water flow (instant, {flow_rate:.2f} L/min)"
                state["flow_debounce"] = 0
            elif flow_rate >= 0.5:
                state["flow_debounce"] += 1
                if state["flow_debounce"] >= 2:
                    interrupted = True
                    reason = f"water flow (sustained, {flow_rate:.2f} L/min)"
                    state["flow_debounce"] = 0
                else:
                    _LOGGER.info(
                        "[auto/%s] Water flow of %.2f L/min detected. Waiting for confirmation (count=%d)",
                        boiler, flow_rate, state["flow_debounce"]
                    )
            else:
                state["flow_debounce"] = 0
                
            if not interrupted:
                if is_gvs:
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
                    old_rate_raw = standby.get(boiler, {}).get(key, 0.0)
                    old_rate = old_rate_raw.get("value", 0.0) if isinstance(old_rate_raw, dict) else float(old_rate_raw or 0.0)
                    new_rate = self._apply_ema(old_rate, rate, alpha=0.1)
                    
                    self.calibration_sensor.update_calibration_coefficient(
                        "overnight_loss",
                        {
                            boiler: {key: new_rate},
                            "last_calibrated": dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
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
    # Passive Overnight Thermal Loss Calibration (01:00 AM - 05:00 AM)
    # Newton's Law LUT — температурно-брэкетная таблица 5°C шаг
    # =========================================================================

    # Состояние state machine для overnight мониторинга (in-memory, не персистентное)
    _overnight_state: dict = {}   # {"gas": {...}, "elec": {...}}
    _overnight_pending: dict = {} # накопленные обновления LUT до финализации

    async def _async_overnight_start(self, _now=None):
        """Запуск ночного мониторинга в 01:00 — инициализация state machine."""
        if self._is_auto_standby_enabled():
            return

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
        if self._is_auto_standby_enabled():
            await self._async_auto_standby_poll()
            return

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
                    old_rate_raw = standby.get(boiler, {}).get(key, 0.0)
                    old_rate = old_rate_raw.get("value", 0.0) if isinstance(old_rate_raw, dict) else float(old_rate_raw or 0.0)
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
        if self._is_auto_standby_enabled():
            return

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
                            old_rate_raw = standby.get(boiler, {}).get(key, 0.0)
                            old_rate = old_rate_raw.get("value", 0.0) if isinstance(old_rate_raw, dict) else float(old_rate_raw or 0.0)
                            new_rate = self._apply_ema(old_rate, rate)
                            self._overnight_pending[boiler][key] = new_rate
                            _LOGGER.info(
                                "[overnight/%s] Partial bracket %s finalized: %.4f °C/h (drop=%.2f°C, elapsed=%.2fh)",
                                boiler, key, new_rate, actual_drop, elapsed_h,
                            )

        # --- Записываем все накопленные обновления LUT ---
        date_str    = now.strftime("%Y-%m-%d %H:%M:%S")
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
        self._calibration_task = self.hass.async_create_task(self._async_execute_heating_phase(phase, cal_data))
        return True

    def _is_temp_too_high_for_calibration(self, phase: str, target_delta: float) -> bool:
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
        """Фоновый процесс циклического нагрева, стабилизации и финализации с поддержкой авторестартов."""
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
            return

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
            
        date_str = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
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
                    self._calibration_task = self.hass.async_create_task(self._async_stabilize_and_finalize(phase, calibration_data, remaining))
                    return
                else:
                    _LOGGER.info("Stabilization delay elapsed during reboot. final calculations will run immediately.")
                    self._calibration_task = self.hass.async_create_task(self._async_stabilize_and_finalize(phase, calibration_data, 0.0))
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
        raw_rate = lut.get(key)
        if raw_rate is not None:
            rate_val = raw_rate.get("value") if isinstance(raw_rate, dict) else raw_rate
            if rate_val is not None:
                try:
                    rate_float = float(rate_val)
                    if rate_float > 0:
                        return rate_float
                except (ValueError, TypeError):
                    pass

        # Fallback: ищем ближайший существующий брэкет с ненулевым значением
        # (бойлер мог быть холоднее чем диапазон LUT)
        best_key   = None
        best_delta = float("inf")
        for k, v in lut.items():
            val = v.get("value") if isinstance(v, dict) else v
            if val is None:
                continue
            try:
                val_float = float(val)
                if val_float <= 0:
                    continue
            except (ValueError, TypeError):
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
            best_val = lut[best_key]
            rate_val = best_val.get("value") if isinstance(best_val, dict) else best_val
            if rate_val is not None:
                try:
                    return float(rate_val)
                except (ValueError, TypeError):
                    pass
        return None

    async def _async_dp_plan_changed(self, event):
        """Handle DP plan state changes."""
        await self._async_apply_current_dp_plan()

    async def _async_system_mode_changed(self, event):
        """Handle system mode selection changes (Auto/Manual)."""
        new_state = event.data.get("new_state")
        if new_state:
            self.current_mode = new_state.state
            if self.current_mode.lower() != "manual":
                await self.async_stop_manual_heating()
            await self._async_apply_current_dp_plan()

    async def _async_people_home_changed(self, event) -> None:
        """Handle presence sensor state change."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if old_state is not None and new_state is not None and old_state.state == new_state.state:
            # Ignore attribute changes
            return
        _LOGGER.debug("Occupancy sensor changed state: %s", new_state.state if new_state else "None")
        await self._async_apply_current_dp_plan()

    def _are_people_home(self) -> bool:
        """Check if people are home based on the presence sensor."""
        if not self.people_home_sensor:
            return True
        state = self.hass.states.get(self.people_home_sensor)
        if not state or state.state in (None, "unknown", "unavailable"):
            return True
        val = state.state.lower()
        if val in ("0", "off", "not_home", "false"):
            return False
        try:
            if float(state.state) == 0:
                return False
        except ValueError:
            pass
        return True

    async def _async_apply_current_dp_plan(self):
        """Apply current DP plan recommended actions to physical hardware if in Auto mode."""
        if self.current_mode.lower() != "auto":
            return

        dp_state = self.hass.states.get("sensor.boiler_dp")
        if not dp_state or dp_state.state in ("unknown", "unavailable", "error", "idle_bypass", "NO PATH", "NO CALIB DATA"):
            self._gas_heating_delayed = False
            # Safe default to IDLE mode, closed bypass (OFF)
            await self._async_set_boiler_mode("IDLE", "OFF")
            return

        mode = dp_state.state.upper()
        recommended_bypass = dp_state.attributes.get("recommended_bypass", "OFF").upper()

        # 1. Update self._warm_boiler_bypass_active status with hysteresis using configured difference
        t_min = float(self.config.get("thermostat_set_temp", 45.0))
        t_elec = self._get_elec_temp()
        warm_diff = float(self.config.get(CONF_BOILER_WARM_DIFF, DEFAULT_BOILER_WARM_DIFF))

        # Инициализация флага при первом запуске: если оверрайд еще не был взведен,
        # но клапан байпаса в HA уже открыт, считаем его активным
        if not hasattr(self, "_warm_boiler_bypass_initialized"):
            self._warm_boiler_bypass_initialized = True
            current_valve = self.hass.states.get(self.bypass_valve) if self.bypass_valve else None
            if current_valve and current_valve.state == STATE_ON:
                self._warm_boiler_bypass_active = True

        if t_elec is not None:
            try:
                t_elec_val = float(t_elec)
                threshold_on = max(20.0, t_min - warm_diff)
                threshold_off = max(20.0, t_min - (warm_diff + 1.0))
                
                if getattr(self, "_warm_boiler_bypass_active", False):
                    if t_elec_val < threshold_off:
                        self._warm_boiler_bypass_active = False
                        _LOGGER.info(
                            "EMS Boiler Controller: Electric boiler temperature (%.1f°C) dropped below override threshold (%.1f°C). "
                            "Disabling warm boiler bypass override.",
                            t_elec_val, threshold_off
                        )
                else:
                    is_heater_on = False
                    if self.elec_heater:
                        heater_state = self.hass.states.get(self.elec_heater)
                        is_heater_on = heater_state and heater_state.state == STATE_ON

                    is_heating_active = is_heater_on or ("ELEC" in mode)
                    if t_elec_val >= threshold_on and is_heating_active:
                        self._warm_boiler_bypass_active = True
                        _LOGGER.info(
                            "EMS Boiler Controller: Electric boiler temperature (%.1f°C) reached warm override threshold (%.1f°C). "
                            "Enabling warm boiler bypass override.",
                            t_elec_val, threshold_on
                        )
            except (ValueError, TypeError):
                self._warm_boiler_bypass_active = False
        else:
            self._warm_boiler_bypass_active = False

        # Gas heating and bypass are synchronized: if the electric boiler is warm,
        # we keep the bypass ON and delay the gas boiler.
        if mode in ("GAS", "IDLE"):
            self._gas_heating_delayed = self._warm_boiler_bypass_active
        else:
            self._gas_heating_delayed = False

        if self._gas_heating_delayed:
            await self._async_set_boiler_mode("IDLE", "ON")
            return

        # 2. If gas heating is NOT delayed, apply occupancy override:
        # If the mode is GAS (or GAS_PUMP) and no one is home, override mode to IDLE to save energy.
        if "GAS" in mode and not self._are_people_home():
            _LOGGER.info(
                "EMS Boiler Controller: DP recommends %s, but no one is home. Overriding mode to IDLE",
                mode
            )
            mode = "IDLE"

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
                
                # Determine target bypass:
                # 1. Override: if electric boiler is warm, keep bypass ON (connected)
                if getattr(self, "_warm_boiler_bypass_active", False):
                    target_bypass = "ON"
                    bypass_source = "Warm Electric Boiler Override"
                # 2. If recommended_bypass is provided by DP sensor and is valid, use it directly
                elif recommended_bypass and recommended_bypass.upper() in ("ON", "OFF"):
                    target_bypass = recommended_bypass.upper()
                    bypass_source = "DP solver recommendation"
                # 3. Fallback logic
                else:
                    target_bypass = current_valve.state.upper() if current_valve else "OFF"
                    bypass_source = "Local Fallback Logic"

                if target_bypass in ("ON", "OFF"):
                    target_service = SERVICE_TURN_ON if target_bypass == "ON" else SERVICE_TURN_OFF
                    target_state = STATE_ON if target_bypass == "ON" else STATE_OFF
                    if not current_valve or current_valve.state != target_state:
                        _LOGGER.info(
                            "EMS Boiler Controller: Setting bypass valve from %s to %s (source: %s, mode: %s)",
                            current_valve.state if current_valve else "unknown",
                            target_state,
                            bypass_source,
                            mode
                        )
                        await self.hass.services.async_call(
                            valve_domain,
                            target_service,
                            {ATTR_ENTITY_ID: self.bypass_valve}
                        )

            # 2. Control Electric Heater
            target_heater_state = STATE_OFF
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

            # 3. Control Circulation Pump
            if self.pump:
                current_pump = self.hass.states.get(self.pump)
                
                if mode == "ELEC_PUMP":
                    heater_on = (target_heater_state == STATE_ON)
                    t_elec = self._get_elec_temp()
                    
                    t_max_elec = self._t_max_elec
                    if t_max_elec is None:
                        t_max_elec = float(self.config.get("elec_boiler_max_temp", 70.0))
                        storage = self.storage
                        if storage and self.current_mode.lower() == "auto":
                            t_max_elec = min(t_max_elec, float(getattr(storage, "boiler_auto_temp_limit", 60.0)))
                        
                    if t_elec is None or not heater_on:
                        target_pump_state_logical = False
                    else:
                        if t_elec >= (t_max_elec - 5.0):
                            target_pump_state_logical = True
                        elif t_elec <= (t_max_elec - 10.0):
                            target_pump_state_logical = False
                        else:
                            target_pump_state_logical = (current_pump.state == STATE_ON) if current_pump else False
                elif mode == "ELEC":
                    target_pump_state_logical = False
                else:
                    target_pump_state_logical = "PUMP" in mode

                target_pump_service = SERVICE_TURN_ON if target_pump_state_logical else SERVICE_TURN_OFF
                target_pump_state = STATE_ON if target_pump_state_logical else STATE_OFF
                
                if not current_pump or current_pump.state != target_pump_state:
                    _LOGGER.info("EMS Boiler Controller: Setting circulation pump from %s to %s", current_pump.state if current_pump else "unknown", target_pump_state)
                    await self.hass.services.async_call(
                        SWITCH_DOMAIN,
                        target_pump_service,
                        {ATTR_ENTITY_ID: self.pump}
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
                    target_temp = None
                    dp_state = self.hass.states.get("sensor.boiler_dp")
                    if dp_state and self.current_mode.lower() == "auto":
                        schedule = dp_state.attributes.get("schedule", [])
                        if schedule:
                            target_temp = schedule[0].get("temp_gas_end")
                            
                    if target_temp is None:
                        target_temp = float(self.config.get("gas_boiler_max_temp", 50.0))
                        storage = self.storage
                        if storage and self.current_mode.lower() == "auto":
                            target_temp = min(target_temp, float(getattr(storage, "boiler_auto_temp_limit", 60.0)))
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

    async def _async_solar_deficit_monitor(self, _now=None):
        """Мониторинг 5-минутного скользящего дефицита солнечной генерации."""
        pv_sensor = self.config.get("current_pv_generation")
        load_sensor = self.config.get("current_house_consumption")

        # Если один из датчиков не настроен — пропускаем проверку
        if not pv_sensor or not load_sensor:
            return

        pv_state = self.hass.states.get(pv_sensor)
        load_state = self.hass.states.get(load_sensor)

        if not pv_state or pv_state.state in (None, "unknown", "unavailable"):
            return
        if not load_state or load_state.state in (None, "unknown", "unavailable"):
            return

        # Проверяем режим инвертора: если grid_bypass=True (например buy) —
        # покупка из сети является намеренным действием DP, отсечка неприменима
        try:
            from .const import INVERTER_MODES
            current_physical_mode = None
            
            # Сначала пытаемся получить физический режим из активного селектора инвертора (после маппинга)
            inverter_modes_list = self.config.get("inverter_modes_list")
            if inverter_modes_list:
                mode_state = self.hass.states.get(inverter_modes_list)
                if mode_state and mode_state.state not in (None, "unknown", "unavailable"):
                    current_physical_mode = mode_state.state.lower()

            # Фолбэк: если селектор не настроен или недоступен — берем из первого слота расписания DP
            if not current_physical_mode:
                dp_state = self.hass.states.get("sensor.dp")
                if dp_state:
                    schedule = dp_state.attributes.get("schedule", [])
                    if schedule and isinstance(schedule, list) and len(schedule) > 0:
                        current_physical_mode = schedule[0].get("physical_mode")
                        if current_physical_mode:
                            current_physical_mode = current_physical_mode.lower()

            mode_cfg = INVERTER_MODES.get(current_physical_mode) if current_physical_mode else None
            grid_is_intentional = getattr(mode_cfg, "is_grid_bypass", False) if mode_cfg else False
        except Exception:
            grid_is_intentional = False

        if grid_is_intentional:
            # В режиме с доступом к сети (buy и т.п.) — сбрасываем отсечку если была активна
            if self._solar_deficit_cutoff:
                _LOGGER.info(
                    "EMS Boiler Controller: Grid import is intentional in mode '%s' (is_grid_bypass=True). "
                    "Deactivating solar deficit cutoff.",
                    current_physical_mode
                )
                self._solar_deficit_cutoff = False
                await self._async_apply_current_dp_plan()
            return

        # Определяем пороговый SOC для отмены отсечки
        # (в режиме curtail_pv используем лимит режима, иначе 95.0% для любого режима)
        active_limit = 95.0
        if mode_cfg and getattr(mode_cfg, "curtail_pv", False):
            active_limit = getattr(mode_cfg, "calibration_limit_soc", 90.0) or 90.0

        bat_soc_sensor = self.config.get("battery_soc_sensor")
        bat_soc = None
        if bat_soc_sensor:
            soc_state = self.hass.states.get(bat_soc_sensor)
            if soc_state and soc_state.state not in (None, "unknown", "unavailable"):
                try:
                    bat_soc = float(soc_state.state)
                except (ValueError, TypeError):
                    pass

        if bat_soc is not None:
            # Вводим гистерезис 1.0% по уровню SOC для предотвращения дребезга
            if self._solar_deficit_cutoff:
                should_bypass = bat_soc >= active_limit
            else:
                should_bypass = bat_soc >= (active_limit - 1.0)

            if should_bypass:
                if self._solar_deficit_cutoff:
                    _LOGGER.info(
                        "EMS Boiler Controller: Battery SOC %.1f%% >= limit %.1f%%. "
                        "Deactivating solar deficit cutoff to allow heating.",
                        bat_soc,
                        active_limit
                    )
                    self._solar_deficit_cutoff = False
                    await self._async_apply_current_dp_plan()
                return

        try:
            # Получаем текущие значения в Вт
            raw_pv = float(pv_state.state)
            curr_pv = raw_pv * 1000.0 if raw_pv < 50.0 else raw_pv

            raw_load = float(load_state.state)
            curr_load = raw_load * 1000.0 if raw_load < 50.0 else raw_load
        except (ValueError, TypeError):
            return

        # Инициализация EMA при первом запуске (Cold Start)
        alpha = 0.0645  # 5 минут при 10-секундных интервалах (N = 30)
        if self._avg_pv is None:
            self._avg_pv = curr_pv
        else:
            self._avg_pv = alpha * curr_pv + (1.0 - alpha) * self._avg_pv

        if self._avg_load is None:
            self._avg_load = curr_load
        else:
            self._avg_load = alpha * curr_load + (1.0 - alpha) * self._avg_load

        # Проверяем состояние ТЭНа
        is_heater_on = False
        if self.elec_heater:
            heater_state = self.hass.states.get(self.elec_heater)
            is_heater_on = heater_state and heater_state.state == STATE_ON

        # Логика отсечки с гистерезисом включения/выключения
        if is_heater_on:
            # Если ТЭН включен и средний дефицит (потребление - генерация) >= 500 Вт
            if self._avg_load - self._avg_pv >= 500.0:
                if not self._solar_deficit_cutoff:
                    _LOGGER.info(
                        "EMS Boiler Controller: 5-minute solar deficit detected (Avg Load: %.0f W, Avg PV: %.0f W). Activating solar deficit cutoff.",
                        self._avg_load,
                        self._avg_pv
                    )
                    self._solar_deficit_cutoff = True
                    # Принудительно обновляем физическое состояние
                    await self._async_apply_current_dp_plan()
        else:
            # Если ТЭН выключен, сбрасываем отсечку только когда избыток солнца превышает 2500 Вт
            # (это гарантирует, что ТЭН мощностью 2.5 кВт будет полностью питаться от солнца без АКБ)
            if self._avg_pv - self._avg_load >= 2500.0:
                if self._solar_deficit_cutoff:
                    _LOGGER.info(
                        "EMS Boiler Controller: Solar excess recovered (Avg PV: %.0f W, Avg Load: %.0f W). Deactivating solar deficit cutoff.",
                        self._avg_pv,
                        self._avg_load
                    )
                    self._solar_deficit_cutoff = False
                    # Принудительно возобновляем план
                    await self._async_apply_current_dp_plan()

    async def async_reset_calibration(self, reset_type: str = "all") -> None:
        """Abort any active calibration task, turn off all equipment unconditionally, and reset coefficients."""
        if self._calibration_task and not self._calibration_task.done():
            _LOGGER.info("EMS Reset Calibration: canceling active calibration background task.")
            self._calibration_task.cancel()
            self._calibration_task = None

        if self.calibration_sensor:
            phase = self.calibration_sensor.native_value
            if phase and phase != "idle":
                _LOGGER.info("EMS Reset Calibration: aborting active phase '%s'.", phase)
                try:
                    await self._actuate_heating(phase, turn_on=False)
                except Exception as ex:
                    _LOGGER.error("EMS Reset Calibration: failed to turn off heating: %s", ex)

            # Unconditional safety shutdowns for ТЭН, pump and gas boiler climate
            _LOGGER.info("EMS Reset Calibration: executing safety shutdowns of heaters and pump.")
            try:
                if self.elec_heater:
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
                if self.gas_climate:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        "set_hvac_mode",
                        {ATTR_ENTITY_ID: self.gas_climate, "hvac_mode": "off"}
                    )
            except Exception as ex:
                _LOGGER.error("EMS Reset Calibration: safety shutdowns failed: %s", ex)

            await self.calibration_sensor.async_reset_calibration(reset_type)

