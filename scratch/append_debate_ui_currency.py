import os
from datetime import datetime

debate_path = r"E:\HA_INTEGRATIONS\EMS\DEBATE.md"

now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

debate_text = f"""
## [{now_str}] Задача: Замена жестко зашитой валюты "руб." на динамическую валюту, настроенную в Home Assistant.

### Archi
Предлагаю следующее решение для вывода динамической валюты в Lovelace-карточке:
1. **Динамическое определение валюты в boiler-card.js**:
   - Внутри метода `_showSlotDetails(slot)` определим переменную `currency`:
     ```javascript
     const currency = this._hass && this._hass.config && this._hass.config.currency
       ? (this._hass.config.currency === "RUB" ? " руб." : " " + this._hass.config.currency)
       : " руб.";
     ```
   - Заменим все 5 жестко зашитых строк `" руб."` на переменную `currency` во всплывающем модальном окне подробностей часа (modal popup).
2. **Версионирование и сброс кэша браузера**:
   - Увеличим `CARD_VERSION` в `boiler-card.js` до `"1.8.2"`.
   - Увеличим `VERSION` в `const.py` до `"0.3.3"`. Поскольку `__init__.py` регистрирует Lovelace-ресурсы с кэш-бастингом вида `?v=${{VERSION}}`, увеличение версии интеграции автоматически принудит Home Assistant выгрузить старую закэшированную версию JS-файла и загрузить новую.

### Skeptic
Критикую предложение Archi с точки зрения стабильности работы интерфейса:
1. **Защита от Undefined на этапе загрузки**: В момент инициализации карточки свойство `this._hass` может быть временно неопределенным (`undefined`) или не содержать поля `config`. Проверка `this._hass && this._hass.config && this._hass.config.currency` обязательна, чтобы избежать падения рендеринга с ошибкой `TypeError`.
2. **Символ рубля**: Если в HA задана валюта "RUB", вывод `" руб."` предпочтительнее сырого `" RUB"`, так как интерфейс карточки полностью локализован на русский язык. Предложенное условие `this._hass.config.currency === "RUB" ? " руб." : ...` это учитывает.
3. **Глобальный сброс кэша**: Решение с поднятием версии `VERSION` в `const.py` абсолютно верное, так как оно обновляет Lovelace-ресурс на уровне Home Assistant, исключая необходимость для пользователя вручную чистить кэш браузера или использовать Ctrl+F5.

### Archi (Итерация 2)
Полностью согласен. Проверка на наличие `_hass.config.currency` защитит карточку от падений при медленной загрузке ядра Home Assistant. Версии карточки и интеграции будут синхронно увеличены.

### Skeptic (Итерация 2)
Все потенциальные риски падений устранены, кэш-бастинг настроен верно. Решение готово к внедрению.

### Заключение
Согласовано:
1. Внедрить безопасное определение валюты `currency` на основе настроек `this._hass.config.currency` во всплывающем окне карточки.
2. Поднять версию `CARD_VERSION` в `boiler-card.js` до `1.8.2`.
3. Поднять версию `VERSION` в `const.py` до `0.3.3` для сброса кэша Lovelace ресурсов.
"""

with open(debate_path, "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.strip() + "\n\n" + debate_text.strip() + "\n"

with open(debate_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Debate appended successfully.")
