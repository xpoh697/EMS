# Technical Specification (ТЗ): Energy Management System (EMS) Integration for Home Assistant

This specification describes the first stage of creating a new Energy Management System (EMS) integration for Home Assistant.

## 1. Domain and Identification
- **Domain Name**: `ems`
- **Integration Name**: `Energy Management System (EMS)`

## 2. Configuration Flow and Options Flow Requirements
To ensure flexibility and modular configuration, the integration must implement:
1. **Config Flow**:
   - Initial entry setup where the user defines the name of the integration instance (default: "Energy Management System").
   - Triggers the creation of the integration entry.
2. **Options Flow**:
   - Offers a settings menu to easily navigate between different configuration categories.
   - The first menu category must be **Basic settings** (Основные настройки).
   - Basic settings page must request the following Home Assistant sensor entities and settings:
     - **Total load consumption** (Общее потребление нагрузки): sensor entity (domain: sensor)
     - **PV Forecast today** (Прогноз СЭС на сегодня): sensor entity (domain: sensor)
     - **PV Forecast tomorrow** (Прогноз СЭС на завтра): sensor entity (domain: sensor)
     - **Number of history days for averaging** (Количество дней истории для усреднения): positive integer (range: 1 - 365, default: 14)
     - **Fallback consumption per hour** (Фолбэк потребления в час): decimal value in kWh (range: 0.0 - 100.0, default: 0.5)
     - **Enable debug logging** (Включить отладочное логирование): boolean flag (default: False)

## 3. Localization
The system must support localization in English and Russian, defined in `strings.json` and translated under `translations/ru.json` and `translations/en.json`.

## 4. Basic Files Structure
- `custom_components/ems/manifest.json`: Integration metadata.
- `custom_components/ems/const.py`: Shared constants.
- `custom_components/ems/__init__.py`: Entry setup and unload logic.
- `custom_components/ems/config_flow.py`: Setup and Options flows with menu.
- `custom_components/ems/strings.json`: Translation strings for config/options flow.
- `custom_components/ems/utils.py`: Logging and other utility functions.
- `custom_components/ems/statistics.py`: DB statistics querying and calculations.
- `custom_components/ems/sensor.py`: Home Assistant sensor entities.

## 5. Deployment and Synchronization (Развертывание и Синхронизация)
To deploy the integration files to the Home Assistant server, the following rules must be followed:
1. **Target Path**: All files under `custom_components/ems/` must be synchronized to:
   `\\192.168.100.5\config\custom_components\ems`
2. **Bytecode Cache Reset**: After the files are successfully copied, the compiled bytecode directory `__pycache__` on the server must be deleted:
   `\\192.168.100.5\config\custom_components\ems\__pycache__`
   This is critical to prevent Home Assistant from executing outdated cached Python files.
3. **Deployment Script**: An automated script `deploy.ps1` in the root of the project handles safety checks, file copying (excluding local `__pycache__`), and remote cache cleaning.

## 6. Statistics Management (Работа со статистикой)
To analyze historical energy usage and compute average consumption profiles, the module `custom_components/ems/statistics.py` implements the following procedure:

### `async_get_average_hourly_consumption`
- **Purpose**: Computes average energy consumption in kWh grouped by weekday (0-6) and hour of the day (0-23) based on historical recorder data.
- **Parameters**:
  - `hass`: Home Assistant core instance.
  - `sensor_id` (str): Entity ID of the cumulative energy sensor (must support `sum` statistic).
  - `days` (int): Number of days back to retrieve statistics.
- **Implementation details**:
  - Validates that the `recorder` component is loaded.
  - Fetches hourly cumulative statistics using `statistics_during_period` with statistics type `{"sum"}`.
  - Runs database fetching and mathematical calculations (`_calculate_hourly_averages`) inside the executor thread pool (`hass.async_add_executor_job`) to prevent blocking the Event Loop.
  - Ignores negative consumption deltas (resets/rollbacks) to ensure profile integrity.
  - Returns a dictionary format: `{ weekday (0-6): { hour (0-23): average_kwh } }` or `None` if data is unavailable.

## 7. Sensor Management (Управление сенсорами)
To track and report load energy consumption dynamically, the integration provides the `load_consumption` sensor platform (`custom_components/ems/sensor.py`).

### `EmsLoadConsumptionSensor`
- **Entity ID**: `sensor.load_consumption`
- **State**: Total energy consumption tracked for the current calendar day (sum of hourly consumption).
- **Unit of Measurement**: `kWh`
- **Device Class**: `energy`
- **State Class**: `total_increasing`
- **Attributes**:
  - `today`: An array of 24 decimal values representing actual hourly consumption for the current day.
  - `average_today`: An array of 24 decimal values representing average hourly consumption for the current day of the week, calculated from historical statistics.
  - `average_monday` to `average_sunday`: Arrays of 24 decimal values representing average hourly consumption for each day of the week, calculated from historical statistics.
  - `last_total_value`: The last recorded raw cumulative value of the target sensor.
  - `last_hour`: The last hour slot updated.
  - `last_day`: The last calendar day processed (for midnight transitions).
- **Device Registry Integration**:
  - Registers a Device in Home Assistant Device Registry using `DeviceInfo`.
  - **Identifiers**: `{(DOMAIN, entry_id)}`
  - **Name**: Dynamically set to the title of the config entry (default: `"Energy Management"`).
  - **Manufacturer**: `"Energy Trader System"`
  - **Model**: `"EMS Controller"`
  - **Software Version**: `VERSION` (`0.1.0`)
  - By registering a device, the Home Assistant UI groups all integration entities under a single device card in the "Devices" (Устройства) tab, and changes the setup button label from "Add Hub" (Добавить хаб) to "Add Device" (Добавить устройство).
- **Persistence & Restoration**:
  - Implements `RestoreSensor` to reload its state and attributes upon Home Assistant restarts.
  - On restart, the sensor uses `async_get_last_state()` to restore the hourly values of `today`, `last_total_value`, `last_hour`, and `last_day`.
  - Jumps in consumption are prevented by ensuring that the initial sensor reading after startup sets the baseline tracker without recording delta spikes.
- **Hourly and Midnight Routines**:
  - Checks if the day of the month has changed relative to `last_day` to reset `today` to 24 zeros.
  - Periodically queries the database averages once a day at midnight.

## 8. Logging Utility (Утилита логирования)
To provide user-controlled debugging info, the integration implements a conditional logger in `custom_components/ems/utils.py`.

### `ems_log`
- **Purpose**: Wraps the standard Python logger to filter out `DEBUG` messages unless the `debug` flag is enabled in the integration settings.
- **Parameters**:
  - `hass`: Home Assistant core instance (used to check the cached debug flag).
  - `logger`: The target module's `logging.Logger` instance.
  - `level`: Log level (e.g., `logging.DEBUG`, `logging.INFO`, `logging.WARNING`, `logging.ERROR`).
  - `message`: Log message string.
  - `*args`, `**kwargs`: Forwarded directly to the underlying logging methods (supporting `exc_info=True`, etc.).
- **Caching optimization**:
  - The debug setting is retrieved and cached in `hass.data[DOMAIN]["debug"]` during the integration startup (`async_setup_entry`).
  - Checking the flag is an $O(1)$ memory operation, avoiding DB/ConfigEntry scans.
