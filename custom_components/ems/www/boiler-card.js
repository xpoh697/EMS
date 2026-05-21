/**
 * EMS Boiler Card v1.4.2
 * - DOM строится один раз → нет мерцания при hover
 * - Значения обновляются точечно через textContent/className
 * - Entity IDs загружаются автоматически через WebSocket API интеграции
 *
 * Минимальный YAML:
 *   type: custom:boiler-card
 *   title: Бойлер   (опционально)
 */

const CARD_VERSION = "1.4.2";

// ── CSS ────────────────────────────────────────────────────────────────────
const STYLES = `
  :host { display: block; }
  ha-card {
    background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
    border-radius: 16px;
    overflow: hidden;
    font-family: var(--paper-font-body1_-_font-family, 'Inter', sans-serif);
  }

  /* ─── Header ─────────────────────────────────────────────────────────── */
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 20px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
  }
  .card-header .title  { font-size: 17px; font-weight: 600; color: var(--primary-text-color); }
  .card-header .ver    { font-size: 11px; color: var(--secondary-text-color); opacity: 0.5; }

  /* ─── Loading / Error ────────────────────────────────────────────────── */
  .state-msg {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 12px; padding: 40px 20px;
    color: var(--secondary-text-color); font-size: 14px; text-align: center;
  }
  .state-msg.error { color: var(--error-color); }

  /* ─── Schema container ────────────────────────────────────────────────── */
  .schema-container {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 16px 16px 8px;
  }
  .schema-container .tile {
    flex: 1;
    min-width: 100px;
    max-width: 140px;
  }
  .schema-middle {
    flex: 1;
    height: 110px;
    min-width: 70px;
    max-width: 130px;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .schema-svg {
    position: absolute;
    top: 0; left: 0; width: 100%; height: 100%;
    pointer-events: none;
  }
  .flow-line {
    fill: none;
    stroke: rgba(33, 150, 243, 0.2); /* Semi-transparent blue as baseline */
    stroke-width: 5px;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
    transition: stroke 0.5s ease;
  }
  .flow-line.active {
    stroke: var(--info-color, #2196f3); /* Blue flow by default */
    stroke-dasharray: 8, 10;
    animation: flow-forward 1.2s linear infinite;
  }
  .flow-line.active-reverse {
    stroke: var(--info-color, #2196f3); /* Blue flow by default */
    stroke-dasharray: 8, 10;
    animation: flow-reverse 1.2s linear infinite;
  }
  .flow-line.active.heating {
    stroke: var(--error-color, #f44336); /* Red flow when heating */
  }
  .flow-line.active-reverse.heating {
    stroke: var(--error-color, #f44336); /* Red flow when heating */
  }
  @keyframes flow-forward {
    from { stroke-dashoffset: 18; }
    to { stroke-dashoffset: 0; }
  }
  @keyframes flow-reverse {
    from { stroke-dashoffset: 0; }
    to { stroke-dashoffset: 18; }
  }

  /* Pump and Valve nodes */
  .pump-node, .valve-node {
    position: absolute;
    transform: translate(-50%, -50%);
    width: 32px;
    height: 32px;
    background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
    border: 2px solid rgba(255,255,255,0.12);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 2;
    cursor: default;
    transition: border-color .3s, background-color .3s, color .3s;
  }
  .pump-node { top: 20%; left: 50%; }
  .valve-node { top: 80%; left: 50%; }

  .schema-container.manual-mode .pump-node,
  .schema-container.manual-mode .valve-node {
    cursor: pointer;
  }
  .schema-container.manual-mode .pump-node:hover,
  .schema-container.manual-mode .valve-node:hover {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.25);
  }
  .schema-container.manual-mode .pump-node.on:hover,
  .schema-container.manual-mode .valve-node.on:hover {
    background: rgba(76,175,80,0.2);
  }
  .schema-container.manual-mode .valve-node.warn:hover {
    background: rgba(255,152,0,0.2);
  }

  .pump-node ha-icon, .valve-node ha-icon {
    --mdc-icon-size: 18px;
    color: rgba(255,255,255,0.3);
    transition: color .3s;
  }
  .pump-node.on {
    border-color: var(--success-color, #4caf50);
    background: rgba(76,175,80,0.12);
  }
  .pump-node.on ha-icon {
    color: var(--success-color, #4caf50);
    animation: spin-pump 2s linear infinite;
  }
  .valve-node.on {
    border-color: var(--success-color, #4caf50);
    background: rgba(76,175,80,0.12);
  }
  .valve-node.on ha-icon {
    color: var(--success-color, #4caf50);
  }
  .valve-node.warn {
    border-color: var(--warning-color, #ff9800);
    background: rgba(255,152,0,0.12);
  }
  .valve-node.warn ha-icon {
    color: var(--warning-color, #ff9800);
  }
  @keyframes spin-pump {
    100% { transform: rotate(360deg); }
  }

  .tile {
    background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px; padding: 14px 12px;
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    transition: background .2s;
  }
  .tile ha-icon { --mdc-icon-size: 28px; transition: color .3s; }
  .tile .t-label { font-size: 11px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .5px; }
  .tile .t-value { font-size: 18px; font-weight: 600; color: var(--primary-text-color); }
  .tile .t-sub   { font-size: 12px; color: var(--secondary-text-color); }

  .tile.st-on   ha-icon { color: var(--success-color,  #4caf50); }
  .tile.st-off  ha-icon { color: rgba(255,255,255,.25); }
  .tile.st-warn ha-icon { color: var(--warning-color,  #ff9800); }

  /* ─── Valve status bar ───────────────────────────────────────────────── */
  .valve-bar {
    margin: 0 16px 8px; padding: 10px 14px; border-radius: 10px;
    display: flex; align-items: center; gap: 10px;
    font-size: 13px; font-weight: 500; transition: background .3s, color .3s;
  }
  .valve-bar.open   { background: rgba(76,175,80,.15);  color: var(--success-color, #4caf50); }
  .valve-bar.closed { background: rgba(244,67,54,.15);  color: var(--error-color,   #f44336); }
  .valve-bar ha-icon { --mdc-icon-size: 20px; }
  .valve-bar .vb-label { flex: 1; }
  .valve-bar .vb-state { font-weight: 700; text-transform: uppercase; font-size: 12px; letter-spacing: .5px; }

  /* ─── Mode toggle ────────────────────────────────────────────────────── */
  .mode-row { display: flex; align-items: center; gap: 10px; margin: 4px 16px 12px; }
  .mode-label { font-size: 13px; color: var(--secondary-text-color); flex: 1; }
  .mode-toggle { display: flex; border-radius: 20px; overflow: hidden; border: 1px solid rgba(255,255,255,.12); }
  .mode-toggle button {
    padding: 6px 18px; font-size: 13px; font-weight: 600;
    border: none; background: transparent; color: var(--secondary-text-color);
    cursor: pointer; transition: background .2s, color .2s; outline: none;
  }
  .mode-toggle button.active { background: var(--primary-color); color: #fff; }

  /* ─── Manual controls ────────────────────────────────────────────────── */
  .controls { display: flex; flex-direction: column; gap: 8px; padding: 8px 16px 18px; }
  .ctrl-btn {
    display: flex; align-items: center; gap: 10px;
    padding: 11px 14px; border-radius: 10px;
    border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.04);
    color: var(--primary-text-color); font-size: 14px;
    cursor: pointer; transition: background .2s, border-color .2s;
    width: 100%; text-align: left;
    /* ВАЖНО: pointer-events не сбрасываем — иначе hover будет работать стабильно */
  }
  .ctrl-btn:hover  { background: rgba(255,255,255,.09); }
  .ctrl-btn:active { background: rgba(255,255,255,.14); }
  .ctrl-btn.disabled { opacity: .4; cursor: not-allowed; pointer-events: none; }
  .ctrl-btn ha-icon  { --mdc-icon-size: 20px; color: var(--primary-color); flex-shrink: 0; }
  .ctrl-btn .btn-label { flex: 1; }
  .ctrl-btn .btn-state {
    font-size: 12px; font-weight: 700; text-transform: uppercase;
    padding: 3px 8px; border-radius: 6px; white-space: nowrap;
  }
  .btn-state.on  { background: rgba(76,175,80,.2);   color: var(--success-color, #4caf50); }
  .btn-state.off { background: rgba(255,255,255,.08); color: var(--secondary-text-color); }

  .auto-hint {
    text-align: center; font-size: 12px; color: var(--secondary-text-color);
    opacity: .7; padding: 8px 0 14px;
    display: flex; align-items: center; justify-content: center; gap: 6px;
  }
  .auto-hint ha-icon { --mdc-icon-size: 16px; }

  .divider { height: 1px; background: rgba(255,255,255,.06); margin: 0 16px; }
  .hidden { display: none !important; }
`;

// ── Helpers ────────────────────────────────────────────────────────────────
const fmt1 = (v) => (v != null && !isNaN(+v)) ? (+v).toFixed(1) : "–";
const stateOn = (s) => s && s.state === "on";

// ── Card ───────────────────────────────────────────────────────────────────
class BoilerCard extends HTMLElement {

  constructor() {
    super();
    this.attachShadow({ mode: "open" });

    this._hass          = null;
    this._entities      = null;
    this._configLoading = false;
    this._configError   = null;
    this._cardConfig    = {};
    this._skeletonBuilt = false;   // full DOM built?
    this._prevKey       = null;    // cheap dirty-check string
  }

  // ── Lovelace hooks ───────────────────────────────────────────────────────
  setConfig(config) {
    this._cardConfig = config || {};
    this._renderLoading("Инициализация…");
  }

  set hass(hass) {
    this._hass = hass;

    if (!this._entities && !this._configLoading && !this._configError) {
      this._fetchEntities();
      return;
    }
    if (this._entities) {
      if (!this._skeletonBuilt) {
        this._buildSkeleton();
      }
      this._updateValues();
    }
  }

  getCardSize() { return 5; }

  // ── Skeleton (built ONCE) ────────────────────────────────────────────────
  _buildSkeleton() {
    const sr = this.shadowRoot;
    sr.innerHTML = "";

    const style = document.createElement("style");
    style.textContent = STYLES;
    sr.appendChild(style);

    const card = document.createElement("ha-card");

    // Header
    const hdr = document.createElement("div");
    hdr.className = "card-header";
    hdr.innerHTML = `
      <span class="title">${this._cardConfig.title || "🔥 Бойлер"}</span>
      <span class="ver">v${CARD_VERSION}</span>`;
    card.appendChild(hdr);

    // Schema Container
    const schema = document.createElement("div");
    schema.className = "schema-container";

    const mkTile = (id, icon, label) => {
      const d = document.createElement("div");
      d.className = "tile st-off";
      d.id = id;
      d.innerHTML = `
        <ha-icon icon="${icon}"></ha-icon>
        <span class="t-label">${label}</span>
        <span class="t-value">–</span>
        <span class="t-sub"></span>`;
      return d;
    };

    this._tGas  = mkTile("t-gas",  "mdi:fire",           "Газ. бойлер");
    this._tElec = mkTile("t-elec", "mdi:lightning-bolt", "Элект. бойлер");

    const middle = document.createElement("div");
    middle.className = "schema-middle";
    middle.innerHTML = `
      <svg class="schema-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        <path class="flow-line" id="line-top" d="M 0 20 L 100 20"></path>
        <path class="flow-line" id="line-bottom" d="M 0 80 L 100 80"></path>
      </svg>
      <div class="pump-node" id="schema-pump" title="Циркуляционный насос">
        <ha-icon icon="mdi:pump"></ha-icon>
      </div>
      <div class="valve-node" id="schema-valve" title="Байпасный клапан">
        <ha-icon icon="mdi:pipe-valve"></ha-icon>
      </div>`;

    this._schemaPump  = middle.querySelector("#schema-pump");
    this._schemaValve = middle.querySelector("#schema-valve");
    this._lineTop     = middle.querySelector("#line-top");
    this._lineBottom  = middle.querySelector("#line-bottom");
    this._schemaContainer = schema;

    // Add interactivity to pump/valve nodes on the schema
    this._schemaPump.addEventListener("click", () => {
      const isManual = this._hass.states[this._entities.mode_select]?.state === "Manual";
      if (isManual) {
        this._toggleEntity("switch", this._entities.pump);
      }
    });
    this._schemaValve.addEventListener("click", () => {
      const isManual = this._hass.states[this._entities.mode_select]?.state === "Manual";
      if (isManual) {
        const e = this._entities;
        const domain = e.valve?.split(".")[0] || "switch";
        this._toggleEntity(domain, e.valve);
      }
    });

    schema.appendChild(this._tGas);
    schema.appendChild(middle);
    schema.appendChild(this._tElec);
    card.appendChild(schema);

    // Valve bar
    this._valveBar = document.createElement("div");
    this._valveBar.className = "valve-bar closed";
    this._valveBar.innerHTML = `
      <ha-icon id="vb-icon" icon="mdi:alert-circle-outline"></ha-icon>
      <span class="vb-label">Электробойлер</span>
      <span class="vb-state" id="vb-state">Изолирован</span>`;
    card.appendChild(this._valveBar);

    // Divider
    const div1 = document.createElement("div");
    div1.className = "divider";
    card.appendChild(div1);

    // Mode row
    const modeRow = document.createElement("div");
    modeRow.className = "mode-row";
    modeRow.innerHTML = `
      <span class="mode-label">Режим системы</span>
      <div class="mode-toggle">
        <button id="btn-auto"   data-mode="Auto">Auto</button>
        <button id="btn-manual" data-mode="Manual">Manual</button>
      </div>`;
    card.appendChild(modeRow);

    this._btnAuto   = modeRow.querySelector("#btn-auto");
    this._btnManual = modeRow.querySelector("#btn-manual");

    this._btnAuto.addEventListener("click", () => this._setMode("Auto"));
    this._btnManual.addEventListener("click", () => this._setMode("Manual"));

    // Divider
    const div2 = document.createElement("div");
    div2.className = "divider";
    card.appendChild(div2);

    // Manual controls
    this._controls = document.createElement("div");
    this._controls.className = "controls";

    const mkBtn = (id, icon, label) => {
      const b = document.createElement("button");
      b.className = "ctrl-btn";
      b.id = id;
      b.innerHTML = `
        <ha-icon icon="${icon}"></ha-icon>
        <span class="btn-label">${label}</span>
        <span class="btn-state off">ВЫКЛ</span>`;
      return b;
    };

    this._btnHeater = mkBtn("cb-heater", "mdi:heating-coil",  "ТЭН электробойлера");
    this._btnPump   = mkBtn("cb-pump",   "mdi:pump",          "Циркуляционный насос");
    this._btnValve  = mkBtn("cb-valve",  "mdi:pipe-valve",    "Байпасный клапан");

    this._btnHeater.addEventListener("click", () => this._toggleEntity("switch", this._entities.elec_heater));
    this._btnPump.addEventListener("click",   () => {
      const e = this._entities;
      const valveOn = stateOn(this._hass.states[e.valve]);
      if (valveOn) this._toggleEntity("switch", e.pump);
    });
    this._btnValve.addEventListener("click",  () => {
      const e = this._entities;
      const domain = e.valve?.split(".")[0] || "switch";
      this._toggleEntity(domain, e.valve);
    });

    this._controls.appendChild(this._btnHeater);
    this._controls.appendChild(this._btnPump);
    this._controls.appendChild(this._btnValve);

    // Auto hint
    this._autoHint = document.createElement("div");
    this._autoHint.className = "auto-hint";
    this._autoHint.innerHTML = `<ha-icon icon="mdi:robot-outline"></ha-icon> Управление автоматическое`;

    card.appendChild(this._controls);
    card.appendChild(this._autoHint);

    sr.appendChild(card);
    this._skeletonBuilt = true;
  }

  // ── Update only changed values (NO innerHTML rebuilds) ────────────────────
  _updateValues() {
    if (!this._skeletonBuilt || !this._entities || !this._hass) return;

    const e  = this._entities;
    const st = this._hass.states;

    const gasS   = st[e.gas_climate];
    const elecS  = st[e.elec_heater];
    const powS   = st[e.elec_power];
    const tmpS   = st[e.elec_temp];
    const pumpS  = st[e.pump];
    const valveS = st[e.valve];
    const modeS  = st[e.mode_select];

    // Cheap dirty check — skip re-paint if nothing changed
    const key = [
      gasS?.state, gasS?.attributes?.current_temperature,
      elecS?.state, powS?.state, tmpS?.state,
      pumpS?.state, valveS?.state, modeS?.state,
    ].join("|");
    if (key === this._prevKey) return;
    this._prevKey = key;

    // ── Gas tile ──────────────────────────────────────────────────────────
    const gasTemp   = gasS?.attributes?.current_temperature ?? gasS?.state ?? "–";
    const gasTarget = gasS?.attributes?.temperature ?? "–";
    this._setTile(this._tGas, "st-on", fmt1(gasTemp) + " °C", "цель: " + fmt1(gasTarget) + " °C");

    // ── Elec tile ─────────────────────────────────────────────────────────
    const elecTemp = fmt1(tmpS?.state);
    const elecPowVal = (powS?.state != null && !isNaN(+powS.state)) ? Math.round(+powS.state) : "–";
    const elecCls  = elecS?.state === "on" ? "st-on" : "st-off";
    this._setTile(this._tElec, elecCls, elecTemp + " °C", "мощность: " + elecPowVal + " Вт");

    // ── Pump & Valve Schema Elements ──────────────────────────────────────
    const pumpOn   = stateOn(pumpS);
    const elecConn = stateOn(valveS);

    // Check if Gas climate is active heating
    const gasActive = gasS?.attributes?.hvac_action === "heating" || 
                      (gasS?.state === "heat" && gasTemp !== null && gasTarget !== null && +gasTemp < +gasTarget);

    // Check if Electric boiler is active heating (heater switch is ON)
    const elecActive = elecS?.state === "on";

    // ── Update Top Line (Gas -> Electric, left to right) ──
    if (pumpOn) {
      this._schemaPump.className = "pump-node on";
      if (gasActive) {
        this._lineTop.className.baseVal = "flow-line active heating";
      } else {
        this._lineTop.className.baseVal = "flow-line active";
      }
    } else {
      this._schemaPump.className = "pump-node";
      this._lineTop.className.baseVal = "flow-line";
    }

    // ── Update Bottom Line (Electric -> Gas, right to left) ──
    if (elecConn) {
      this._schemaValve.className = "valve-node on";
      this._schemaValve.querySelector("ha-icon").setAttribute("icon", "mdi:pipe-valve");
      if (pumpOn) {
        if (elecActive) {
          this._lineBottom.className.baseVal = "flow-line active-reverse heating";
        } else {
          this._lineBottom.className.baseVal = "flow-line active-reverse";
        }
      } else {
        this._lineBottom.className.baseVal = "flow-line";
      }
    } else {
      this._schemaValve.className = "valve-node warn";
      this._schemaValve.querySelector("ha-icon").setAttribute("icon", "mdi:alert-circle-outline");
      this._lineBottom.className.baseVal = "flow-line";
    }

    // ── Valve bar ─────────────────────────────────────────────────────────
    this._valveBar.className = "valve-bar " + (elecConn ? "open" : "closed");
    this._valveBar.querySelector("#vb-icon").setAttribute("icon",
      elecConn ? "mdi:lightning-bolt-circle" : "mdi:alert-circle-outline");
    this._valveBar.querySelector("#vb-state").textContent =
      elecConn ? "Подключён" : "Изолирован";

    // ── Mode buttons ──────────────────────────────────────────────────────
    const isManual = modeS?.state === "Manual";
    this._btnAuto.classList.toggle("active", !isManual);
    this._btnManual.classList.toggle("active",  isManual);
    this._schemaContainer.classList.toggle("manual-mode", isManual);

    // ── Show/hide controls vs hint ────────────────────────────────────────
    this._controls.classList.toggle("hidden", !isManual);
    this._autoHint.classList.toggle("hidden",  isManual);

    if (isManual) {
      // Heater button
      this._updateCtrlBtn(this._btnHeater, elecS?.state === "on");
      // Pump button (disabled if valve off)
      this._updateCtrlBtn(this._btnPump, pumpOn);
      this._btnPump.classList.toggle("disabled", !elecConn);
      // Valve button
      this._updateCtrlBtn(this._btnValve, elecConn, elecConn ? "Подключён" : "Изолирован");
    }
  }

  // ── DOM helpers ──────────────────────────────────────────────────────────
  _setTile(tile, stClass, value, sub) {
    // Remove all st-* classes, add the new one
    tile.className = "tile " + stClass;
    tile.querySelector(".t-value").textContent = value;
    tile.querySelector(".t-sub").textContent   = sub;
  }

  _updateCtrlBtn(btn, isOn, customLabel) {
    const badge = btn.querySelector(".btn-state");
    badge.className = "btn-state " + (isOn ? "on" : "off");
    badge.textContent = customLabel ?? (isOn ? "ВКЛ" : "ВЫКЛ");
  }

  _setMode(mode) {
    if (!this._entities?.mode_select || !this._hass) return;
    this._hass.callService("select", "select_option", {
      entity_id: this._entities.mode_select,
      option: mode,
    });
  }

  _toggleEntity(domain, entityId) {
    if (!entityId || !this._hass) return;
    this._hass.callService(domain, "toggle", { entity_id: entityId });
  }

  // ── WebSocket fetch (once) ───────────────────────────────────────────────
  async _fetchEntities() {
    this._configLoading = true;
    this._renderLoading("Загружаем конфиг из EMS…");

    try {
      const msg = { type: "ems/get_boiler_config" };
      if (this._cardConfig.entry_id) msg.entry_id = this._cardConfig.entry_id;

      const result = await this._hass.connection.sendMessagePromise(msg);

      // Debug: log what we got
      console.info("[boiler-card] WS config received:", result);

      this._entities    = result;
      this._configError = null;
    } catch (err) {
      this._configError = err.message || "Не удалось получить конфиг из EMS";
      this._renderError(this._configError);
    } finally {
      this._configLoading = false;
    }

    if (this._entities) {
      this._buildSkeleton();
      this._updateValues();
    }
  }

  // ── Simple state screens ─────────────────────────────────────────────────
  _renderLoading(msg) {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="card-header">
          <span class="title">${this._cardConfig.title || "🔥 Бойлер"}</span>
          <span class="ver">v${CARD_VERSION}</span>
        </div>
        <div class="state-msg">
          <ha-circular-progress active indeterminate></ha-circular-progress>
          <span>${msg}</span>
        </div>
      </ha-card>`;
    this._skeletonBuilt = false;
  }

  _renderError(msg) {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="card-header">
          <span class="title">${this._cardConfig.title || "🔥 Бойлер"}</span>
          <span class="ver">v${CARD_VERSION}</span>
        </div>
        <div class="state-msg error">
          <ha-icon icon="mdi:alert-circle-outline"></ha-icon>
          <span>${msg}</span>
        </div>
      </ha-card>`;
    this._skeletonBuilt = false;
  }
}

// ── Registration ─────────────────────────────────────────────────────────────
customElements.define("boiler-card", BoilerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "boiler-card",
  name: "EMS Boiler Card",
  description: "Plug-and-play карточка бойлера — entity_id загружаются из интеграции EMS автоматически.",
  preview: true,
});

console.info(
  `%c EMS Boiler Card %c v${CARD_VERSION} `,
  "background:#1976d2;color:#fff;border-radius:4px 0 0 4px;padding:2px 6px;font-weight:bold;",
  "background:#333;color:#fff;border-radius:0 4px 4px 0;padding:2px 6px;"
);
