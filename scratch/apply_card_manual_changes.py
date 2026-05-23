import os

file_path = r"E:\HA_INTEGRATIONS\EMS\custom_components\ems\www\boiler-card.js"

with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update Version to 1.6.0
code = code.replace('const CARD_VERSION = "1.5.0";', 'const CARD_VERSION = "1.6.0";', 1)
code = code.replace('v1.5.0', 'v1.6.0', 1)

# 2. Add CSS Styles
target1 = """  .divider { height: 1px; background: rgba(255,255,255,.06); margin: 0 16px; }
  .hidden { display: none !important; }

  /* ─── Schedule Tab Styling ───────────────────────────────────────────── */"""

replacement1 = """  .divider { height: 1px; background: rgba(255,255,255,.06); margin: 0 16px; }
  .hidden { display: none !important; }

  /* ─── Manual heating panel ───────────────────────────────────────────── */
  .manual-heat-panel {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 8px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .manual-heat-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--primary-color, #2196f3);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .ctrl-select-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ctrl-select-row label {
    font-size: 11px;
    color: var(--secondary-text-color);
  }
  .ctrl-select-row select {
    background: #2c2c2e;
    color: #fff;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    padding: 8px;
    font-size: 13px;
    outline: none;
    cursor: pointer;
    font-family: inherit;
  }
  .ctrl-slider-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ctrl-slider-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .ctrl-slider-header label {
    font-size: 11px;
    color: var(--secondary-text-color);
  }
  .ctrl-slider-header .slider-val {
    font-size: 13px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .ctrl-slider-row input[type="range"] {
    width: 100%;
    margin: 6px 0;
    accent-color: var(--primary-color, #2196f3);
    cursor: pointer;
  }
  .ctrl-estimate {
    font-size: 12px;
    font-weight: 600;
    color: var(--info-color, #2196f3);
    background: rgba(33,150,243,0.08);
    padding: 6px 10px;
    border-radius: 6px;
    text-align: center;
  }
  .ctrl-start-btn {
    background: var(--primary-color, #2196f3);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    text-align: center;
    transition: background-color 0.2s;
  }
  .ctrl-start-btn:hover {
    background: #1976d2;
  }
  .ctrl-start-btn.active {
    background: var(--error-color, #f44336);
  }
  .ctrl-start-btn.active:hover {
    background: #d32f2f;
  }

  /* ─── Schedule Tab Styling ───────────────────────────────────────────── */"""

if target1 not in code:
    raise ValueError("Target 1 not found in boiler-card.js")
code = code.replace(target1, replacement1, 1)

# 3. Add Manual Heat Panel DOM and Listeners to _buildSkeleton()
target2 = """    // Manual controls
    this._controls = document.createElement("div");
    this._controls.className = "controls";

    const mkBtn = (id, icon, label) => {"""

replacement2 = """    // Manual controls
    this._controls = document.createElement("div");
    this._controls.className = "controls";

    // Manual heating panel
    this._manualHeatPanel = document.createElement("div");
    this._manualHeatPanel.className = "manual-heat-panel";
    this._manualHeatPanel.innerHTML = `
      <div class="manual-heat-title">Ручной нагрев</div>
      
      <div class="ctrl-select-row">
        <label>Режим нагрева</label>
        <select id="mh-mode">
          <option value="GAS">GAS - gas only</option>
          <option value="GAS_PUMP">GAS_PUMP - Gas + pump</option>
          <option value="ELEC">ELEC - Electro only</option>
          <option value="ELEC_PUMP">Elec_pump - eclectro + pump</option>
        </select>
      </div>

      <div class="ctrl-slider-row">
        <div class="ctrl-slider-header">
          <label>Целевая температура</label>
          <span class="slider-val" id="mh-setpoint-val">50.0 °C</span>
        </div>
        <input type="range" id="mh-setpoint" min="40" max="70" step="0.5" value="50">
      </div>

      <div class="ctrl-estimate" id="mh-estimate">Расход газа: –</div>
      
      <button class="ctrl-start-btn" id="mh-start-btn">Старт</button>
    `;

    this._mhModeSelect = this._manualHeatPanel.querySelector("#mh-mode");
    this._mhSetpointSlider = this._manualHeatPanel.querySelector("#mh-setpoint");
    this._mhSetpointVal = this._manualHeatPanel.querySelector("#mh-setpoint-val");
    this._mhEstimate = this._manualHeatPanel.querySelector("#mh-estimate");
    this._mhStartBtn = this._manualHeatPanel.querySelector("#mh-start-btn");

    this._mhModeSelect.addEventListener("change", () => {
      const mode = this._mhModeSelect.value;
      const e = this._entities;
      if (e && e.valve && this._hass) {
        const currentValveState = this._hass.states[e.valve]?.state;
        const targetState = mode === "GAS" ? "off" : "on";
        if (currentValveState && currentValveState !== targetState) {
          const domain = e.valve.split(".")[0];
          const service = targetState === "on" ? "turn_on" : "turn_off";
          this._hass.callService(domain, service, { entity_id: e.valve });
        }
      }
      this._updateSliderLimits();
      this._recalcEstimate();
    });

    this._mhSetpointSlider.addEventListener("input", () => {
      this._mhSetpointVal.textContent = parseFloat(this._mhSetpointSlider.value).toFixed(1) + " °C";
      this._recalcEstimate();
    });

    this._mhStartBtn.addEventListener("click", () => {
      const active = this._mhStartBtn.classList.contains("active");
      if (active) {
        this._hass.callService("ems", "stop_manual_heating", {});
      } else {
        const mode = this._mhModeSelect.value;
        const setpoint = parseFloat(this._mhSetpointSlider.value);
        this._hass.callService("ems", "start_manual_heating", { mode, setpoint });
      }
    });

    this._controls.appendChild(this._manualHeatPanel);

    const mkBtn = (id, icon, label) => {"""

if target2 not in code:
    raise ValueError("Target 2 not found in boiler-card.js")
code = code.replace(target2, replacement2, 1)

# 4. Insert helper methods in BoilerCard
target3 = """  // ── DOM helpers ──────────────────────────────────────────────────────────
  _setTile(tile, stClass, value, sub, rawTemp) {"""

replacement3 = """  _updateSliderLimits() {
    if (!this._hass || !this._entities) return;
    const dpS = this._hass.states["sensor.boiler_dp"];
    if (!dpS || !dpS.attributes) return;

    const t_min = dpS.attributes.t_min ?? 45.0;
    const t_max_gas = dpS.attributes.t_max_gas ?? 50.0;
    const t_max_elec = dpS.attributes.t_max_elec ?? 70.0;

    const mode = this._mhModeSelect.value;
    const t_max = (mode.includes("GAS")) ? t_max_gas : t_max_elec;

    this._mhSetpointSlider.min = t_min;
    this._mhSetpointSlider.max = t_max;
    
    let currentVal = parseFloat(this._mhSetpointSlider.value);
    if (currentVal > t_max) {
      this._mhSetpointSlider.value = t_max;
      this._mhSetpointVal.textContent = t_max.toFixed(1) + " °C";
    } else if (currentVal < t_min) {
      this._mhSetpointSlider.value = t_min;
      this._mhSetpointVal.textContent = t_min.toFixed(1) + " °C";
    }
  }

  _recalcEstimate() {
    if (!this._hass || !this._entities) return;
    
    const e = this._entities;
    const st = this._hass.states;
    const calS = st["sensor.boiler_calibration"];
    
    const mode = this._mhModeSelect.value;
    const setpoint = parseFloat(this._mhSetpointSlider.value);
    
    let currentTemp = 20.0;
    let text = "–";
    
    if (mode.includes("GAS")) {
      const gasS = st[e.gas_climate];
      currentTemp = parseFloat(gasS?.attributes?.current_temperature ?? gasS?.state ?? 20.0);
      const eff = (mode === "GAS_PUMP") 
        ? (calS?.attributes?.gas_with_pump?.efficiency_c_per_m3 || 80.0)
        : (calS?.attributes?.gas_only?.efficiency_c_per_m3 || 80.0);
      
      const safe_eff = eff > 0.0 ? eff : 80.0;
      const delta_T = Math.max(0.0, setpoint - currentTemp);
      const expected = delta_T / safe_eff;
      text = "Расход газа: " + expected.toFixed(2) + " м³";
    } else {
      const elecTempS = st[e.elec_temp];
      currentTemp = parseFloat(elecTempS?.state ?? 20.0);
      const eff = (mode === "ELEC_PUMP")
        ? (calS?.attributes?.elec_with_pump?.efficiency_c_per_kwh || 8.6)
        : (calS?.attributes?.elec_only?.efficiency_c_per_kwh || 8.6);
        
      const safe_eff = eff > 0.0 ? eff : 8.6;
      const delta_T = Math.max(0.0, setpoint - currentTemp);
      const expected = delta_T / safe_eff;
      text = "Расход энергии: " + expected.toFixed(2) + " кВт";
    }
    
    this._mhEstimate.textContent = text;
  }

  // ── DOM helpers ──────────────────────────────────────────────────────────
  _setTile(tile, stClass, value, sub, rawTemp) {"""

if target3 not in code:
    raise ValueError("Target 3 not found in boiler-card.js")
code = code.replace(target3, replacement3, 1)

# 5. Update _updateValues to handle manual heating state
target4 = """    if (isManual) {
      // Heater button
      this._updateCtrlBtn(this._btnHeater, elecS?.state === "on");"""

replacement4 = """    if (isManual) {
      const manualActive = dpS?.attributes?.manual_heating_active === true;
      if (manualActive) {
        this._mhModeSelect.disabled = true;
        this._mhSetpointSlider.disabled = true;
        this._mhStartBtn.textContent = "Стоп";
        this._mhStartBtn.classList.add("active");
        
        const mMode = dpS.attributes.manual_heating_mode;
        const mSetpoint = dpS.attributes.manual_heating_setpoint;
        if (mMode) this._mhModeSelect.value = mMode;
        if (mSetpoint != null) {
          this._mhSetpointSlider.value = mSetpoint;
          this._mhSetpointVal.textContent = parseFloat(mSetpoint).toFixed(1) + " °C";
        }
      } else {
        this._mhModeSelect.disabled = false;
        this._mhSetpointSlider.disabled = false;
        this._mhStartBtn.textContent = "Старт";
        this._mhStartBtn.classList.remove("active");
      }
      this._updateSliderLimits();
      this._recalcEstimate();

      // Heater button
      this._updateCtrlBtn(this._btnHeater, elecS?.state === "on");"""

if target4 not in code:
    raise ValueError("Target 4 not found in boiler-card.js")
code = code.replace(target4, replacement4, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("boiler-card.js modified successfully.")
