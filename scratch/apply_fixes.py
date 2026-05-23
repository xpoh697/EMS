import os

# 1. Modify custom_components/ems/boiler_dp_engine.py
engine_path = "custom_components/ems/boiler_dp_engine.py"
with open(engine_path, "r", encoding="utf-8") as f:
    engine_code = f.read()

target_idle = """                        # Grid index represents T_gas
                        curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                        if 0 <= curr_idx < num_states:
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            if T_curr >= t_min_limit and T_curr <= t_max_mode:
                                if T_active >= t_min_limit:"""

repl_idle = """                        # Grid index represents T_gas
                        curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                        if 0 <= curr_idx < num_states:
                            T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                            if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                if T_active >= t_min_limit:"""

target_elec = """                            t_max_mode = t_max
                            curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                            if 0 <= curr_idx < num_states:
                                T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                                if T_curr >= t_min_limit and T_curr <= t_max_mode:
                                    if T_active >= t_min_limit:"""

repl_elec = """                            t_max_mode = t_max
                            curr_idx = int(round((T_gas_end_val - GRID_MIN_TEMP) * 2))
                            if 0 <= curr_idx < num_states:
                                T_curr = GRID_MIN_TEMP + curr_idx * 0.5
                                if T_curr >= GRID_MIN_TEMP and T_curr <= t_max_mode:
                                    if T_active >= t_min_limit:"""

if target_idle in engine_code and target_elec in engine_code:
    engine_code = engine_code.replace(target_idle, repl_idle)
    engine_code = engine_code.replace(target_elec, repl_elec)
    with open(engine_path, "w", encoding="utf-8") as f:
        f.write(engine_code)
    print("boiler_dp_engine.py modified successfully!")
else:
    print("Error: Targets not found in boiler_dp_engine.py")


# 2. Modify custom_components/ems/sensor.py
sensor_path = "custom_components/ems/sensor.py"
with open(sensor_path, "r", encoding="utf-8") as f:
    sensor_code = f.read()

target_listeners = """        config = self._entry.data
        options = self._entry.options
        price_buy_sensor_id = options.get(CONF_PRICE_BUY_SENSOR, config.get(CONF_PRICE_BUY_SENSOR))
        price_sell_sensor_id = options.get(CONF_PRICE_SELL_SENSOR, config.get(CONF_PRICE_SELL_SENSOR))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))

        # Recalculate on SOC changes with throttling
        if bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_soc_entity_id], self._async_soc_listener
                )
            )

        # Listen for tariff and forecast changes
        generic_listeners = []
        if price_buy_sensor_id:
            generic_listeners.append(price_buy_sensor_id)
        if price_sell_sensor_id:
            generic_listeners.append(price_sell_sensor_id)
        generic_listeners.extend([
            "sensor.pv_forecast_today",
            "sensor.pv_forecast_tomorrow"
        ])

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, generic_listeners, self._async_generic_listener
            )
        )"""

repl_listeners = """        config = self._entry.data
        options = self._entry.options
        price_buy_sensor_id = options.get(CONF_PRICE_BUY_SENSOR, config.get(CONF_PRICE_BUY_SENSOR))
        price_sell_sensor_id = options.get(CONF_PRICE_SELL_SENSOR, config.get(CONF_PRICE_SELL_SENSOR))
        bat_soc_entity_id = options.get(CONF_BAT_SOC_ENTITY, config.get(CONF_BAT_SOC_ENTITY))
        total_load_consumption_id = options.get(CONF_TOTAL_LOAD_CONSUMPTION, config.get(CONF_TOTAL_LOAD_CONSUMPTION))

        # Recalculate on SOC changes with throttling
        if bat_soc_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [bat_soc_entity_id], self._async_soc_listener
                )
            )

        # Listen for tariff, consumption and forecast changes
        generic_listeners = []
        if price_buy_sensor_id:
            generic_listeners.append(price_buy_sensor_id)
        if price_sell_sensor_id:
            generic_listeners.append(price_sell_sensor_id)
        if total_load_consumption_id:
            generic_listeners.append(total_load_consumption_id)
        generic_listeners.extend([
            "sensor.pv_forecast_today",
            "sensor.pv_forecast_tomorrow"
        ])

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, generic_listeners, self._async_generic_listener
            )
        )"""

target_debounce = """        # Debounce/cooldown check
        if not force and self._reactive_debounce_time is not None:
            cooldown_rem = (now - self._reactive_debounce_time).total_seconds()
            if cooldown_rem < 60:
                ems_log(
                    self.hass,
                    _LOGGER,
                    logging.DEBUG,
                    f"EMS DP: update debounced (cooldown: {60 - cooldown_rem:.1f}s remaining)"
                )
                return

        self._reactive_debounce_time = now

        start_time = time.perf_counter()"""

repl_debounce = """        # Debounce/cooldown check
        if not force and self._reactive_debounce_time is not None:
            cooldown_rem = (now - self._reactive_debounce_time).total_seconds()
            if cooldown_rem < 60:
                ems_log(
                    self.hass,
                    _LOGGER,
                    logging.DEBUG,
                    f"EMS DP: update debounced (cooldown: {60 - cooldown_rem:.1f}s remaining)"
                )
                return

        start_time = time.perf_counter()"""

target_log_missing_param = """                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Required configuration parameter '{key}' is missing! Please configure it in integration settings."
                    )"""

repl_log_missing_param = """                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.WARNING,
                        f"Required configuration parameter '{key}' is missing! Please configure it in integration settings."
                    )"""

target_log_unavailable_sensor = """                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.ERROR,
                        f"Required sensor '{entity_id}' is in state '{state_obj.state if state_obj else 'None'}'. Skipping strategy update."
                    )"""

repl_log_unavailable_sensor = """                    ems_log(
                        self.hass,
                        _LOGGER,
                        logging.WARNING,
                        f"Required sensor '{entity_id}' is in state '{state_obj.state if state_obj else 'None'}'. Skipping strategy update."
                    )"""

target_execute_job = """            storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
            overrides = storage.get_overrides()

            result = await self.hass.async_add_executor_job("""

repl_execute_job = """            storage = self.hass.data[DOMAIN][self._entry_id]["storage"]
            overrides = storage.get_overrides()

            self._reactive_debounce_time = now
            result = await self.hass.async_add_executor_job("""

mod_count = 0
if target_listeners in sensor_code:
    sensor_code = sensor_code.replace(target_listeners, repl_listeners)
    mod_count += 1
else:
    print("Warning: target_listeners not found in sensor.py")

if target_debounce in sensor_code:
    sensor_code = sensor_code.replace(target_debounce, repl_debounce)
    mod_count += 1
else:
    print("Warning: target_debounce not found in sensor.py")

if target_log_missing_param in sensor_code:
    sensor_code = sensor_code.replace(target_log_missing_param, repl_log_missing_param)
    mod_count += 1
else:
    print("Warning: target_log_missing_param not found in sensor.py")

if target_log_unavailable_sensor in sensor_code:
    sensor_code = sensor_code.replace(target_log_unavailable_sensor, repl_log_unavailable_sensor)
    mod_count += 1
else:
    print("Warning: target_log_unavailable_sensor not found in sensor.py")

if target_execute_job in sensor_code:
    sensor_code = sensor_code.replace(target_execute_job, repl_execute_job)
    mod_count += 1
else:
    print("Warning: target_execute_job not found in sensor.py")

if mod_count == 5:
    with open(sensor_path, "w", encoding="utf-8") as f:
        f.write(sensor_code)
    print("sensor.py modified successfully!")
else:
    print(f"Error: only {mod_count}/5 targets modified in sensor.py")
