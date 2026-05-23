import os

debate_path = r"E:\HA_INTEGRATIONS\EMS\DEBATE.md"

debate_text = """
## [2026-05-23 17:27] Задача: Сохранение состояния байпасного клапана в режиме IDLE без изменений.

### Archi
Предлагаю доработать логику управления байпасным клапаном для поддержки режима `"IDLE"`:
1. В режиме `"GAS"` байпас принудительно закрывается (`"OFF"`).
2. В режимах нагрева (`"ELEC"`, `"ELEC_PUMP"`, `"GAS_PUMP"`) байпас принудительно открывается (`"ON"`).
3. В режиме `"IDLE"` (и всех прочих, не связанных с нагревом) байпасный клапан должен оставаться в своем текущем состоянии (не изменяется).
4. Реализовать это в `_async_set_boiler_mode` через проверку:
   - Если `mode == "GAS"` -> `target_bypass = "OFF"`
   - Если `"ELEC" in mode or "_PUMP" in mode` -> `target_bypass = "ON"`
   - Иначе -> `target_bypass = current_valve.state` (то есть состояние не меняется, и сервис управления не вызывается).

### Skeptic
Критикую предложение Archi:
1. **Обработка отсутствующего текущего состояния клапана**: Если в режиме `"IDLE"` при первом старте `current_valve` вернет `None` (например, датчик еще не восстановился), выражение `current_valve.state` вызовет `AttributeError`.
   *Archi's counter-measure*: Мы добавим безопасную проверку: `target_bypass = current_valve.state.upper() if current_valve else None`. Если состояние неопределено, мы просто пропускаем управление клапаном.
2. **Безопасность насоса**: Если в `"IDLE"` байпас остался закрытым (`OFF`), насос все равно выключен (поскольку в режиме нет суффикса `_PUMP`), поэтому опасности гидроудара или перегрузки насоса нет.

### Archi (Итерация 2)
Согласен. Добавление безопасного извлечения `current_valve.state.upper() if current_valve else None` гарантирует защиту от исключений при запуске Home Assistant.

### Skeptic (Итерация 2)
Изменения полностью безопасны. Алгоритм управления байпасом теперь в точности отражает физические требования гидравлической схемы во всех режимах. Решение готово.

### Заключение
Согласовано:
1. В режиме `"IDLE"` сохранять состояние байпаса неизменным.
2. Использовать конструкцию `target_bypass = current_valve.state.upper() if current_valve else None` для безопасного пропуска отправки команд в `"IDLE"`.
"""

with open(debate_path, "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.strip() + "\n" + debate_text

with open(debate_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Debate appended successfully.")
