import os

const_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\const.py"
card_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\www\boiler-card.js"

# --- 1. Modify const.py version ---
with open(const_path, "r", encoding="utf-8") as f:
    const_content = f.read()

target_version = 'VERSION = "0.3.2"'
replacement_version = 'VERSION = "0.3.3"'

if target_version in const_content:
    const_content = const_content.replace(target_version, replacement_version, 1)
    with open(const_path, "w", encoding="utf-8") as f:
        f.write(const_content)
    print("const.py version bumped to 0.3.3.")
else:
    print("Warning: target_version not found in const.py")

# --- 2. Modify boiler-card.js ---
with open(card_path, "r", encoding="utf-8") as f:
    card_content = f.read()

# Bump CARD_VERSION
target_card_ver = 'const CARD_VERSION = "1.8.1";'
replacement_card_ver = 'const CARD_VERSION = "1.8.2";'

if target_card_ver in card_content:
    card_content = card_content.replace(target_card_ver, replacement_card_ver, 1)
    print("boiler-card.js version bumped to 1.8.2.")
else:
    print("Warning: target_card_ver not found in boiler-card.js")

# Modify modal slot details to use dynamic currency
target_slot_details = """  _showSlotDetails(slot) {
    const titleEl = this._modalOverlay.querySelector("#modal-title");
    const contentEl = this._modalOverlay.querySelector("#modal-content");
    
    const hourStr = String(slot.hour).padStart(2, '0') + ":00";
    titleEl.textContent = `Час: ${hourStr}`;
    
    const modeLabels = {
      "IDLE": "Простой (IDLE)",
      "GAS": "Нагрев газом (GAS)",
      "GAS_PUMP": "Газ + Насос (GAS_PUMP)",
      "ELEC": "Нагрев ТЭН (ELEC)",
      "ELEC_PUMP": "ТЭН + Насос (ELEC_PUMP)",
      "PUMP_ONLY": "Только Насос (PUMP_ONLY)"
    };
    
    const bypassLabel = slot.bypass ? "Открыт (Последовательный)" : "Закрыт (Байпас)";
    
    contentEl.innerHTML = `
      <div class="modal-row-detail">
        <span class="m-label">Режим системы</span>
        <span class="m-val">${modeLabels[slot.mode] || slot.mode}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Байпасный клапан</span>
        <span class="m-val">${bypassLabel}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Планируемый расход</span>
        <span class="m-val">${slot.cost != null ? slot.cost.toFixed(2) + " руб." : "0.00 руб."}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Энергия</span>
        <span class="m-val">${slot.energy != null ? slot.energy.toFixed(2) + " кВтч/м³" : "0.0 кВтч/м³"}</span>
      </div>
      
      <div class="modal-section-title">Планируемые температуры</div>
      <div class="modal-row-detail">
        <span class="m-label">Газовый бойлер</span>
        <span class="m-val">${fmt1(slot.temp_gas_start)}°C → ${fmt1(slot.temp_gas_end)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электробойлер</span>
        <span class="m-val">${fmt1(slot.temp_elec_start)}°C → ${fmt1(slot.temp_elec_end)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">ГВС (на выходе)</span>
        <span class="m-val" style="color: ${getTempColor(slot.temp_active_end)}">${fmt1(slot.temp_active_start)}°C → ${fmt1(slot.temp_active_end)}°C</span>
      </div>

      <div class="modal-section-title">Стоимость нагрева на 1°C</div>
      <div class="modal-row-detail">
        <span class="m-label">Газ (без насоса)</span>
        <span class="m-val">${slot.cost_per_c_gas != null ? slot.cost_per_c_gas.toFixed(2) + " руб." : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Газ + Насос</span>
        <span class="m-val">${slot.cost_per_c_gas_pump != null ? slot.cost_per_c_gas_pump.toFixed(2) + " руб." : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электро (без насоса)</span>
        <span class="m-val">${slot.cost_per_c_elec != null ? slot.cost_per_c_elec.toFixed(2) + " руб." : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электро + Насос</span>
        <span class="m-val">${slot.cost_per_c_elec_pump != null ? slot.cost_per_c_elec_pump.toFixed(2) + " руб." : "–"}</span>
      </div>
    `;"""

replacement_slot_details = """  _showSlotDetails(slot) {
    const titleEl = this._modalOverlay.querySelector("#modal-title");
    const contentEl = this._modalOverlay.querySelector("#modal-content");
    
    const hourStr = String(slot.hour).padStart(2, '0') + ":00";
    titleEl.textContent = `Час: ${hourStr}`;
    
    const modeLabels = {
      "IDLE": "Простой (IDLE)",
      "GAS": "Нагрев газом (GAS)",
      "GAS_PUMP": "Газ + Насос (GAS_PUMP)",
      "ELEC": "Нагрев ТЭН (ELEC)",
      "ELEC_PUMP": "ТЭН + Насос (ELEC_PUMP)",
      "PUMP_ONLY": "Только Насос (PUMP_ONLY)"
    };
    
    const bypassLabel = slot.bypass ? "Открыт (Последовательный)" : "Закрыт (Байпас)";
    
    const currency = this._hass && this._hass.config && this._hass.config.currency
      ? (this._hass.config.currency === "RUB" ? " руб." : " " + this._hass.config.currency)
      : " руб.";

    contentEl.innerHTML = `
      <div class="modal-row-detail">
        <span class="m-label">Режим системы</span>
        <span class="m-val">${modeLabels[slot.mode] || slot.mode}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Байпасный клапан</span>
        <span class="m-val">${bypassLabel}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Планируемый расход</span>
        <span class="m-val">${slot.cost != null ? slot.cost.toFixed(2) + currency : "0.00" + currency}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Энергия</span>
        <span class="m-val">${slot.energy != null ? slot.energy.toFixed(2) + " кВтч/м³" : "0.0 кВтч/м³"}</span>
      </div>
      
      <div class="modal-section-title">Планируемые температуры</div>
      <div class="modal-row-detail">
        <span class="m-label">Газовый бойлер</span>
        <span class="m-val">${fmt1(slot.temp_gas_start)}°C → ${fmt1(slot.temp_gas_end)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электробойлер</span>
        <span class="m-val">${fmt1(slot.temp_elec_start)}°C → ${fmt1(slot.temp_elec_end)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">ГВС (на выходе)</span>
        <span class="m-val" style="color: ${getTempColor(slot.temp_active_end)}">${fmt1(slot.temp_active_start)}°C → ${fmt1(slot.temp_active_end)}°C</span>
      </div>

      <div class="modal-section-title">Стоимость нагрева на 1°C</div>
      <div class="modal-row-detail">
        <span class="m-label">Газ (без насоса)</span>
        <span class="m-val">${slot.cost_per_c_gas != null ? slot.cost_per_c_gas.toFixed(2) + currency : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Газ + Насос</span>
        <span class="m-val">${slot.cost_per_c_gas_pump != null ? slot.cost_per_c_gas_pump.toFixed(2) + currency : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электро (без насоса)</span>
        <span class="m-val">${slot.cost_per_c_elec != null ? slot.cost_per_c_elec.toFixed(2) + currency : "–"}</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Электро + Насос</span>
        <span class="m-val">${slot.cost_per_c_elec_pump != null ? slot.cost_per_c_elec_pump.toFixed(2) + currency : "–"}</span>
      </div>
    `;"""

if target_slot_details in card_content:
    card_content = card_content.replace(target_slot_details, replacement_slot_details, 1)
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(card_content)
    print("boiler-card.js details modal code updated successfully.")
else:
    print("Warning: target_slot_details not found in boiler-card.js")
