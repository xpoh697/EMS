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
   - Basic settings page must request the following Home Assistant sensor entities:
     - **Total load consumption** (Общее потребление нагрузки): sensor entity
     - **PV Forecast today** (Прогноз СЭС на сегодня): sensor entity
     - **PV Forecast tomorrow** (Прогноз СЭС на завтра): sensor entity

## 3. Localization
The system must support localization in English and Russian, defined in `strings.json` and translated under `translations/ru.json` and `translations/en.json`.

## 4. Basic Files Structure
- `custom_components/ems/manifest.json`: Integration metadata.
- `custom_components/ems/const.py`: Shared constants.
- `custom_components/ems/__init__.py`: Entry setup and unload logic.
- `custom_components/ems/config_flow.py`: Setup and Options flows with menu.
- `custom_components/ems/strings.json`: Translation strings for config/options flow.
