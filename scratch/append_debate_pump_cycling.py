import os
from datetime import datetime

debate_path = r"E:\HA_INTEGRATIONS\EMS\DEBATE.md"

now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

debate_text = f"""
## [{now_str}] Задача: Помпа в режиме ELEC_PUMP должна работать циклически (включаться при setpoint-5 и выключаться при setpoint-10), а не постоянно.

### Archi
Предлагаю следующее решение для циклического управления насосом в режиме `ELEC_PUMP` в авто-режиме:
1. **Управление насосом в `_async_set_boiler_mode`**:
   Изменим условие включения насоса. Если в плановом режиме системы содержится `"ELEC"` (т.е. `ELEC` или `ELEC_PUMP`), состояние насоса должно определяться флагом динамической перекачки `self._elec_pump_dump_active`. В противном случае насос управляется по признаку `"PUMP" in mode` (для `GAS_PUMP` и `PUMP_ONLY`):
   ```python
   if "ELEC" in mode:
       target_pump_state_logical = self._elec_pump_dump_active
   else:
       target_pump_state_logical = "PUMP" in mode
   ```
2. **Коррекция контроля ручного вмешательства в `_async_override_check`**:
   Соответствующим образом скорректируем ожидаемое состояние насоса при проверке ручного управления. Если плановый режим содержит `"ELEC"`, ожидаемым состоянием насоса должно быть `STATE_ON`, если активен флаг перекачки `self._elec_pump_dump_active`, и `STATE_OFF` в противном случае. Это предотвратит принудительное включение насоса обратно в `ON` модулем автоотката:
   ```python
   elif entity_id == self.pump:
       if "ELEC" in mode:
           expected_state = STATE_ON if self._elec_pump_dump_active else STATE_OFF
       else:
           expected_state = STATE_ON if ("PUMP" in mode and recommended_bypass == "ON") else STATE_OFF
   ```

### Skeptic
Критикую архитектурное предложение Archi и выделяю три SRE/QA-риска:
1. **Инициализация флага при запуске**: Флаг `self._elec_pump_dump_active` изначально инициализируется в `False`. Если при старте системы температура электрического котла уже высока, мы должны гарантировать актуальность флага до первого включения ТЭНа или насоса.
   *Archi's counter-measure*: Метод `_update_cutoff_states` всегда вызывается в самом начале `_async_set_boiler_mode`, обновляя флаги на основе текущих температур непосредственно перед любым действием.
2. **Влияние на ручной режим нагрева**: Ручной режим `ELEC_PUMP` имеет собственную независимую ветку управления в `_async_apply_manual_heating`, использующую `self._manual_pump_dump_active`. Важно убедиться, что изменения в автоматической части не затрагивают ручной режим.
   *Archi's counter-measure*: Автоматический и ручной режимы полностью изолированы: `_async_apply_manual_heating` не использует логику `_async_set_boiler_mode`.
3. **Защита от тактования при сбоях датчиков**: Если датчик температуры электробойлера возвращает `None` или уходит в ошибку, флаг `self._elec_pump_dump_active` должен безопасно сбрасываться в `False`, чтобы предотвратить «сухой» или нецелевой прогон насоса. В `_update_cutoff_states` это поведение уже заложено.

### Archi (Итерация 2)
Принимаю замечания Skeptic. Подтверждаю, что вызов `_update_cutoff_states` гарантирует актуальность всех флагов при любых запусках или изменениях планов, а ручной режим нагрева работает по изолированной ветке и не подвержен влиянию данных изменений.

### Skeptic (Итерация 2)
Корректировки и анализ подтверждают безопасность изменений. Решение готово к внедрению.

### Заключение
Согласовано:
1. Изменить логику вычисления `target_pump_state_logical` в `_async_set_boiler_mode`: при наличии `"ELEC"` в названии режима использовать статус `self._elec_pump_dump_active`.
2. Изменить проверку `expected_state` для насоса в `_async_override_check` аналогичным образом.
"""

with open(debate_path, "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.strip() + "\n\n" + debate_text.strip() + "\n"

with open(debate_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Debate appended successfully.")
