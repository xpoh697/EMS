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

## 5. Deployment and Synchronization (Развертывание и Синхронизация)
To deploy the integration files to the Home Assistant server, the following rules must be followed:
1. **Target Path**: All files under `custom_components/ems/` must be synchronized to:
   `\\192.168.100.5\config\custom_components\ems`
2. **Bytecode Cache Reset**: After the files are successfully copied, the compiled bytecode directory `__pycache__` on the server must be deleted:
   `\\192.168.100.5\config\custom_components\ems\__pycache__`
   This is critical to prevent Home Assistant from executing outdated cached Python files.
3. **Deployment Script**: An automated script `deploy.ps1` in the root of the project handles safety checks, file copying (excluding local `__pycache__`), and remote cache cleaning.

