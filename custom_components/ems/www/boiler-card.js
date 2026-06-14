/**
 * EMS Boiler Card v1.6.0
 * - DOM строится один раз → нет мерцания при hover
 * - Значения обновляются точечно через textContent/className
 * - Entity IDs загружаются автоматически через WebSocket API интеграции
 * - Добавлена вкладка "Расписание" (Schedule) с почасовой сеткой и детальным модальным окном
 * - Изменены цвета иконок ТЭНа и горелки: красный при нагреве, серый при простое
 */

const CARD_VERSION = "1.9.2";

// ── CSS ────────────────────────────────────────────────────────────────────
const STYLES = `
  :host { display: block; }
  ha-card {
    background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
    border-radius: 16px;
    overflow: hidden;
    font-family: var(--paper-font-body1_-_font-family, 'Inter', sans-serif);
    position: relative;
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

  .vacation-banner {
    background: rgba(244, 67, 54, 0.12);
    border: 1px solid rgba(244, 67, 54, 0.3);
    border-radius: 12px;
    padding: 10px 14px;
    margin: 10px 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    color: #ef5350;
    font-size: 13px;
    font-weight: 600;
    box-shadow: 0 4px 12px rgba(244, 67, 54, 0.08);
  }
  .vacation-banner ha-icon {
    --mdc-icon-size: 18px;
    color: #ef5350;
    animation: plane-bounce 2s infinite ease-in-out;
  }
  @keyframes plane-bounce {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-3px); }
  }

  /* ─── Tabs ───────────────────────────────────────────────────────────── */
  .card-tabs {
    display: flex;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    background: rgba(255,255,255,0.02);
  }
  .card-tab {
    flex: 1;
    text-align: center;
    padding: 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--secondary-text-color);
    cursor: pointer;
    transition: color 0.2s, background-color 0.2s;
    border-bottom: 2px solid transparent;
  }
  .card-tab.active {
    color: var(--primary-color, #2196f3);
    border-bottom-color: var(--primary-color, #2196f3);
    background: rgba(255,255,255,0.04);
  }

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
    height: 140px;
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
  .pump-node { top: 15%; left: 50%; }
  .valve-node { top: 57%; left: 50%; }
  .hw-pump-node {
    position: absolute;
    top: 85%; left: 50%;
    transform: translate(-50%, -50%);
    width: 28px;
    height: 28px;
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
  .hw-pump-node ha-icon {
    --mdc-icon-size: 16px;
    color: rgba(255,255,255,0.3);
    transition: color .3s;
  }
  .hw-pump-node.on {
    border-color: var(--success-color, #4caf50);
    background: rgba(76,175,80,0.12);
  }
  .hw-pump-node.on ha-icon {
    color: var(--success-color, #4caf50);
    animation: spin-pump-reverse 2s linear infinite;
  }
  @keyframes spin-pump-reverse {
    100% { transform: rotate(-360deg); }
  }
  .hw-return-temp {
    position: absolute;
    top: 95%; left: 50%;
    transform: translateX(-50%);
    font-size: 10px;
    color: var(--secondary-text-color);
    background: var(--ha-card-background, #1c1c1e);
    padding: 0 4px;
    cursor: pointer;
    transition: color .2s;
  }
  .hw-return-temp:hover {
    text-decoration: underline;
    color: var(--primary-color, #2196f3);
  }

  .schema-container.manual-mode .pump-node,
  .schema-container.manual-mode .valve-node,
  .schema-container.manual-mode .hw-pump-node {
    cursor: pointer;
  }
  .schema-container.manual-mode .pump-node:hover,
  .schema-container.manual-mode .valve-node:hover,
  .schema-container.manual-mode .hw-pump-node:hover {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.25);
  }
  .schema-container.manual-mode .pump-node.on:hover,
  .schema-container.manual-mode .valve-node.on:hover,
  .schema-container.manual-mode .hw-pump-node.on:hover {
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
    transition: background .2s, border-color .2s;
    cursor: pointer;
  }
  .tile:hover {
    background: rgba(255,255,255,0.07);
    border-color: rgba(255,255,255,0.12);
  }
  .tile ha-icon { --mdc-icon-size: 28px; transition: color .3s; }
  .tile .t-label { font-size: 11px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .5px; }
  .tile .t-value { font-size: 18px; font-weight: 600; color: var(--primary-text-color); }
  .tile .t-sub   { font-size: 12px; color: var(--secondary-text-color); }

  /* Red when heating, light gray when idle/off */
  .tile.st-heating ha-icon { color: var(--error-color, #f44336); }
  .tile.st-idle    ha-icon { color: rgba(255, 255, 255, 0.4); }

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

  .mode-row { display: flex; align-items: center; gap: 10px; margin: 4px 16px 12px; }
  .mode-label { font-size: 13px; color: var(--secondary-text-color); flex: 1; }
  .setpoint-row { display: flex; align-items: center; gap: 10px; margin: 4px 16px 12px; }
  .setpoint-label { font-size: 13px; color: var(--secondary-text-color); flex: 1; }
  .setpoint-value { font-size: 13px; font-weight: 600; color: var(--primary-color, #2196f3); }
  .status-info-row { display: flex; align-items: center; gap: 10px; margin: 4px 16px 12px; }
  .status-info-label { font-size: 13px; color: var(--secondary-text-color); flex: 1; }
  .status-info-value { font-size: 13px; font-weight: 600; color: var(--primary-text-color); }
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

  /* ─── Schedule Tab Styling ───────────────────────────────────────────── */
  .schedule-day-header {
    font-size: 14px;
    font-weight: 700;
    color: var(--primary-color, #2196f3);
    margin: 16px 16px 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-left: 3px solid var(--primary-color, #2196f3);
    padding-left: 8px;
  }
  .schedule-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    padding: 0 16px 16px;
  }
  .schedule-tile {
    border-radius: 8px;
    padding: 10px 4px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    cursor: pointer;
    transition: background .2s, border-color .2s, transform .1s;
    border: 1px solid transparent;
  }
  .schedule-tile:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 8px rgba(0,0,0,0.15);
  }
  .schedule-tile:active {
    transform: translateY(0);
  }
  .schedule-tile .st-hour {
    font-size: 12px;
    font-weight: 600;
  }
  .schedule-tile .st-icons {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
  }
  .schedule-tile .st-icons ha-icon {
    --mdc-icon-size: 20px;
  }
  .schedule-tile .st-icons ha-icon.pump-icon {
    --mdc-icon-size: 14px;
    animation: spin-pump 3s linear infinite;
  }
  .schedule-tile .st-mode {
    font-size: 9px;
    font-weight: 700;
    padding: 2px 4px;
    border-radius: 4px;
    text-align: center;
    white-space: nowrap;
    text-transform: uppercase;
  }

  /* Themes */
  .schedule-tile.theme-idle {
    background: rgba(255,255,255,0.03);
    border-color: rgba(255,255,255,0.06);
    color: var(--secondary-text-color);
  }
  .schedule-tile.theme-idle .st-hour {
    color: var(--secondary-text-color);
    opacity: 0.8;
  }
  .schedule-tile.theme-idle .st-icons ha-icon {
    color: var(--secondary-text-color);
    opacity: 0.5;
  }

  .schedule-tile.theme-gas {
    background: rgba(255,152,0,0.08);
    border-color: rgba(255,152,0,0.25);
    color: #ff9800;
  }
  .schedule-tile.theme-gas .st-hour {
    color: #ffb74d;
  }
  .schedule-tile.theme-gas .st-icons ha-icon {
    color: #ff9800;
  }

  .schedule-tile.theme-gas_pump {
    background: rgba(255,152,0,0.15);
    border-color: rgba(255,152,0,0.4);
    color: #ffb74d;
  }
  .schedule-tile.theme-gas_pump .st-hour {
    color: #ffe0b2;
  }
  .schedule-tile.theme-gas_pump .st-icons ha-icon {
    color: #ff9800;
  }

  .schedule-tile.theme-elec {
    background: rgba(76,175,80,0.08);
    border-color: rgba(76,175,80,0.25);
    color: #4caf50;
  }
  .schedule-tile.theme-elec .st-hour {
    color: #81c784;
  }
  .schedule-tile.theme-elec .st-icons ha-icon {
    color: #4caf50;
  }

  .schedule-tile.theme-elec_pump {
    background: rgba(76,175,80,0.15);
    border-color: rgba(76,175,80,0.4);
    color: #81c784;
  }
  .schedule-tile.theme-elec_pump .st-hour {
    color: #c8e6c9;
  }
  .schedule-tile.theme-elec_pump .st-icons ha-icon {
    color: #4caf50;
  }

  .schedule-tile.theme-pump_only {
    background: rgba(33,150,243,0.12);
    border-color: rgba(33,150,243,0.3);
    color: #64b5f6;
  }
  .schedule-tile.theme-pump_only .st-hour {
    color: #bbdefb;
  }
  .schedule-tile.theme-pump_only .st-icons ha-icon {
    color: #2196f3;
  }

  .mode-idle { background: rgba(255,255,255,0.06); color: var(--secondary-text-color); }
  .mode-gas { background: rgba(255,152,0,0.12); color: #ff9800; }
  .mode-gas_pump { background: rgba(255,152,0,0.22); color: #ffb74d; border: 1px dashed #ff9800; }
  .mode-elec { background: rgba(76,175,80,0.12); color: #4caf50; }
  .mode-elec_pump { background: rgba(76,175,80,0.22); color: #81c784; border: 1px dashed #4caf50; }
  .mode-pump_only { background: rgba(33,150,243,0.18); color: #64b5f6; border: 1px dashed #2196f3; }

  /* ─── Modal Dialog Styling ───────────────────────────────────────────── */
  .modal-overlay {
    position: fixed;
    top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(0,0,0,0.65);
    backdrop-filter: blur(5px);
    z-index: 999;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
  }
  .modal-overlay.open {
    opacity: 1;
    pointer-events: auto;
  }
  .modal-window {
    background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 16px;
    width: 90%;
    max-width: 340px;
    padding: 14px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.6);
    transform: scale(0.9);
    transition: transform 0.25s ease;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .modal-overlay.open .modal-window {
    transform: scale(1);
  }
  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    padding-bottom: 6px;
  }
  .modal-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--primary-text-color);
  }
  .modal-close {
    background: transparent;
    border: none;
    color: var(--secondary-text-color);
    cursor: pointer;
    padding: 2px;
    display: flex;
    align-items: center;
  }
  .modal-close ha-icon {
    --mdc-icon-size: 18px;
  }
  .modal-content {
    font-size: 12px;
    color: var(--primary-text-color);
    display: flex;
    flex-direction: column;
    gap: 5px;
    max-height: 75vh;
    overflow-y: auto;
    scrollbar-width: none;
  }
  .modal-content::-webkit-scrollbar {
    display: none;
  }
  .modal-row-detail {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }
  .modal-row-detail .m-label {
    color: var(--secondary-text-color);
  }
  .modal-row-detail .m-val {
    font-weight: 600;
    white-space: nowrap;
  }
  .modal-section-title {
    font-size: 10px;
    font-weight: 700;
    color: var(--primary-color, #2196f3);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  /* ─── Schedule calc info footer ─────────────────────────────────────── */
  .sched-calc-info {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 6px;
    padding: 8px 16px 12px;
    font-size: 11px;
    color: var(--secondary-text-color);
    opacity: 0.7;
  }
  .sched-calc-info ha-icon {
    --mdc-icon-size: 13px;
    opacity: 0.6;
  }
  .sched-calc-dur {
    background: rgba(255,255,255,0.06);
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 10px;
    font-family: monospace;
    color: var(--primary-color, #2196f3);
  }

  /* ─── Auto settings panel ────────────────────────────────────────────── */
  .auto-settings-panel {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 12px;
    margin: 8px 16px 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .auto-settings-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--primary-color, #2196f3);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .auto-settings-row {
    display: flex;
    gap: 12px;
  }
  .auto-select-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .auto-select-col label {
    font-size: 11px;
    color: var(--secondary-text-color);
  }
  .auto-select-col select {
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
`;

// ── Helpers ────────────────────────────────────────────────────────────────
const fmt1 = (v) => (v != null && !isNaN(+v)) ? (+v).toFixed(1) : "–";
const stateOn = (s) => s && s.state === "on";

const getTempColor = (t) => {
  if (t == null || isNaN(parseFloat(t))) return "var(--primary-text-color)";
  const temp = parseFloat(t);
  if (temp <= 20) return "rgb(33, 150, 243)";
  if (temp >= 45) {
    const pct = Math.min(1.0, (temp - 45) / (80 - 45));
    const r = Math.round(244 + (168 - 244) * pct);
    const g = Math.round(67 + (16 - 67) * pct);
    const b = Math.round(54 + (50 - 54) * pct);
    return `rgb(${r}, ${g}, ${b})`;
  }
  const pct = (temp - 20) / (45 - 20);
  const r = Math.round(33 + (244 - 33) * pct);
  const g = Math.round(150 + (67 - 150) * pct);
  const b = Math.round(243 + (54 - 243) * pct);
  return `rgb(${r}, ${g}, ${b})`;
};

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
    this._scheduleNeedsUpdate = true;
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

  getCardSize() { return 6; }

  // ── Skeleton (built ONCE) ────────────────────────────────────────────────
  _buildSkeleton() {
    const sr = this.shadowRoot;
    sr.innerHTML = "";

    const style = document.createElement("style");
    style.textContent = STYLES;
    sr.appendChild(style);

    const card = document.createElement("ha-card");

    // Vacation banner
    const vacBanner = document.createElement("div");
    vacBanner.id = "vacation-banner";
    vacBanner.className = "vacation-banner hidden";
    vacBanner.innerHTML = `
      <ha-icon icon="mdi:airplane"></ha-icon>
      <span>Режим отпуска активирован</span>`;
    card.appendChild(vacBanner);

    // Header
    const hdr = document.createElement("div");
    hdr.className = "card-header";
    hdr.innerHTML = `
      <span class="title">${this._cardConfig.title || "🔥 Бойлер"}</span>
      <span class="ver">v${CARD_VERSION}</span>`;
    card.appendChild(hdr);

    // Tabs Container
    const tabsContainer = document.createElement("div");
    tabsContainer.className = "card-tabs";
    tabsContainer.innerHTML = `
      <div class="card-tab active" id="tab-status">Состояние</div>
      <div class="card-tab" id="tab-schedule">Расписание</div>
    `;
    card.appendChild(tabsContainer);

    // Status Tab Content
    this._statusContent = document.createElement("div");
    this._statusContent.className = "tab-content-status";

    // Schema Container
    const schema = document.createElement("div");
    schema.className = "schema-container";

    const mkTile = (id, icon, label) => {
      const d = document.createElement("div");
      d.className = "tile st-idle";
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
      <svg class="schema-svg" viewBox="0 0 100 140" preserveAspectRatio="none">
        <path class="flow-line" id="line-top" d="M 0 21 L 100 21"></path>
        <path class="flow-line" id="line-bottom" d="M 0 80 L 100 80"></path>
        <path class="flow-line" id="line-hw-circ" d="M 15 119 L 85 119"></path>
      </svg>
      <div class="pump-node" id="schema-pump" title="Насос загрузки бойлера">
        <ha-icon icon="mdi:pump"></ha-icon>
      </div>
      <div class="valve-node" id="schema-valve" title="Байпасный клапан">
        <ha-icon icon="mdi:pipe-valve"></ha-icon>
      </div>
      <div class="hw-pump-node" id="schema-hw-pump" title="Циркуляционный насос ГВС">
        <ha-icon icon="mdi:pump"></ha-icon>
      </div>
      <div class="hw-return-temp" id="schema-hw-temp"></div>`;

    this._schemaPump  = middle.querySelector("#schema-pump");
    this._schemaValve = middle.querySelector("#schema-valve");
    this._schemaHwPump = middle.querySelector("#schema-hw-pump");
    this._schemaHwTemp = middle.querySelector("#schema-hw-temp");
    this._lineTop     = middle.querySelector("#line-top");
    this._lineBottom  = middle.querySelector("#line-bottom");
    this._lineHwCirc  = middle.querySelector("#line-hw-circ");
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
    this._schemaHwPump.addEventListener("click", () => {
      const isManual = this._hass.states[this._entities.mode_select]?.state === "Manual";
      if (isManual && this._entities.hw_pump) {
        this._toggleEntity("switch", this._entities.hw_pump);
      }
    });

    this._tGas.addEventListener("click", () => {
      this._openMoreInfo(this._entities?.gas_climate);
    });
    this._tElec.addEventListener("click", () => {
      this._openMoreInfo(this._entities?.elec_temp);
    });
    this._schemaHwTemp.addEventListener("click", () => {
      this._openMoreInfo(this._entities?.hw_return_temp);
    });

    schema.appendChild(this._tGas);
    schema.appendChild(middle);
    schema.appendChild(this._tElec);
    this._statusContent.appendChild(schema);

    // Valve bar
    this._valveBar = document.createElement("div");
    this._valveBar.className = "valve-bar closed";
    this._valveBar.innerHTML = `
      <ha-icon id="vb-icon" icon="mdi:alert-circle-outline"></ha-icon>
      <span class="vb-label">Электробойлер</span>
      <span class="vb-state" id="vb-state">Изолирован</span>`;
    this._statusContent.appendChild(this._valveBar);

    // Divider
    const div1 = document.createElement("div");
    div1.className = "divider";
    this._statusContent.appendChild(div1);

    // Mode row
    const modeRow = document.createElement("div");
    modeRow.className = "mode-row";
    modeRow.innerHTML = `
      <span class="mode-label">Режим системы</span>
      <div class="mode-toggle">
        <button id="btn-auto"   data-mode="Auto">Auto</button>
        <button id="btn-manual" data-mode="Manual">Manual</button>
      </div>`;
    this._statusContent.appendChild(modeRow);

    this._btnAuto   = modeRow.querySelector("#btn-auto");
    this._btnManual = modeRow.querySelector("#btn-manual");

    this._btnAuto.addEventListener("click", () => this._setMode("Auto"));
    this._btnManual.addEventListener("click", () => this._setMode("Manual"));

    // Setpoint row
    const setpointRow = document.createElement("div");
    setpointRow.className = "setpoint-row";
    setpointRow.innerHTML = `
      <span class="setpoint-label">Текущий сетпоинт</span>
      <span class="setpoint-value" id="setpoint-val">–</span>`;
    this._statusContent.appendChild(setpointRow);
    this._setpointVal = setpointRow.querySelector("#setpoint-val");

    // System Average row
    const sysAvgRow = document.createElement("div");
    sysAvgRow.className = "status-info-row";
    sysAvgRow.innerHTML = `
      <span class="status-info-label">Средняя температура системы</span>
      <span class="status-info-value" id="sys-avg-val">–</span>`;
    this._statusContent.appendChild(sysAvgRow);
    this._sysAvgVal = sysAvgRow.querySelector("#sys-avg-val");

    // DHW Outlet row
    const dhwOutletRow = document.createElement("div");
    dhwOutletRow.className = "status-info-row";
    dhwOutletRow.innerHTML = `
      <span class="status-info-label">ГВС на выходе из системы</span>
      <span class="status-info-value" id="dhw-outlet-val">–</span>`;
    this._statusContent.appendChild(dhwOutletRow);
    this._dhwOutletVal = dhwOutletRow.querySelector("#dhw-outlet-val");

    // House Flow row
    const houseFlowRow = document.createElement("div");
    houseFlowRow.className = "status-info-row";
    houseFlowRow.innerHTML = `
      <span class="status-info-label">Температура подачи в дом</span>
      <span class="status-info-value" id="house-flow-val">–</span>`;
    this._statusContent.appendChild(houseFlowRow);
    this._houseFlowVal = houseFlowRow.querySelector("#house-flow-val");

    // Base Limit Today row
    const baseLimitTodayRow = document.createElement("div");
    baseLimitTodayRow.className = "status-info-row hidden";
    baseLimitTodayRow.innerHTML = `
      <span class="status-info-label">Базовый лимит сегодня</span>
      <span class="status-info-value" id="base-limit-today-val">–</span>`;
    this._statusContent.appendChild(baseLimitTodayRow);
    this._baseLimitTodayVal = baseLimitTodayRow.querySelector("#base-limit-today-val");

    // Solar Surplus Today row
    const solarSurplusTodayRow = document.createElement("div");
    solarSurplusTodayRow.className = "status-info-row hidden";
    solarSurplusTodayRow.innerHTML = `
      <span class="status-info-label">Избыток СЭС сегодня</span>
      <span class="status-info-value" id="solar-surplus-today-val">–</span>`;
    this._statusContent.appendChild(solarSurplusTodayRow);
    this._solarSurplusTodayVal = solarSurplusTodayRow.querySelector("#solar-surplus-today-val");

    // PV Used Today row
    const pvUsedTodayRow = document.createElement("div");
    pvUsedTodayRow.className = "status-info-row hidden";
    pvUsedTodayRow.innerHTML = `
      <span class="status-info-label">Использовано сегодня</span>
      <span class="status-info-value" id="pv-used-today-val">–</span>`;
    this._statusContent.appendChild(pvUsedTodayRow);
    this._pvUsedTodayVal = pvUsedTodayRow.querySelector("#pv-used-today-val");

    // PV Remaining Today row
    const pvRemainingTodayRow = document.createElement("div");
    pvRemainingTodayRow.className = "status-info-row hidden";
    pvRemainingTodayRow.innerHTML = `
      <span class="status-info-label">Осталось сегодня</span>
      <span class="status-info-value" id="pv-remaining-today-val">–</span>`;
    this._statusContent.appendChild(pvRemainingTodayRow);
    this._pvRemainingTodayVal = pvRemainingTodayRow.querySelector("#pv-remaining-today-val");

    // Base Limit Tomorrow row
    const baseLimitTomorrowRow = document.createElement("div");
    baseLimitTomorrowRow.className = "status-info-row hidden";
    baseLimitTomorrowRow.innerHTML = `
      <span class="status-info-label">Базовый лимит завтра</span>
      <span class="status-info-value" id="base-limit-tomorrow-val">–</span>`;
    this._statusContent.appendChild(baseLimitTomorrowRow);
    this._baseLimitTomorrowVal = baseLimitTomorrowRow.querySelector("#base-limit-tomorrow-val");

    // Solar Surplus Tomorrow row
    const solarSurplusTomorrowRow = document.createElement("div");
    solarSurplusTomorrowRow.className = "status-info-row hidden";
    solarSurplusTomorrowRow.innerHTML = `
      <span class="status-info-label">Избыток СЭС завтра</span>
      <span class="status-info-value" id="solar-surplus-tomorrow-val">–</span>`;
    this._statusContent.appendChild(solarSurplusTomorrowRow);
    this._solarSurplusTomorrowVal = solarSurplusTomorrowRow.querySelector("#solar-surplus-tomorrow-val");

    // Divider
    const div2 = document.createElement("div");
    div2.className = "divider";
    this._statusContent.appendChild(div2);

    // Manual controls
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
          <option value="PUMP_ONLY">PUMP_ONLY - Pump only (mixing)</option>
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
      const val = parseFloat(this._mhSetpointSlider.value).toFixed(1) + " °C";
      this._mhSetpointVal.textContent = val;
      if (this._setpointVal) {
        this._setpointVal.textContent = val;
      }
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
    this._btnPump   = mkBtn("cb-pump",   "mdi:pump",          "Насос загрузки бойлера");
    this._btnValve  = mkBtn("cb-valve",  "mdi:pipe-valve",    "Байпасный клапан");
    this._btnHwPump = mkBtn("cb-hw-pump","mdi:water-pump",    "Циркуляционный насос ГВС");

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
    this._btnHwPump.addEventListener("click", () => {
      if (this._entities.hw_pump) this._toggleEntity("switch", this._entities.hw_pump);
    });

    this._controls.appendChild(this._btnHeater);
    this._controls.appendChild(this._btnPump);
    this._controls.appendChild(this._btnValve);
    this._controls.appendChild(this._btnHwPump);

    // Auto Settings Panel (Schedule Start/End selection)
    this._autoSettingsPanel = document.createElement("div");
    this._autoSettingsPanel.className = "auto-settings-panel hidden";
    this._autoSettingsPanel.innerHTML = `
      <div class="auto-settings-title">Часы работы нагрева</div>
      <div class="auto-settings-row">
        <div class="auto-select-col">
          <label>С</label>
          <select id="auto-start-hour">
            ${Array.from({length: 24}, (_, i) => `<option value="${i}">${String(i).padStart(2, '0')}:00</option>`).join("")}
          </select>
        </div>
        <div class="auto-select-col">
          <label>По</label>
          <select id="auto-end-hour">
            ${Array.from({length: 24}, (_, i) => `<option value="${i}">${String(i).padStart(2, '0')}:00</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="ctrl-slider-row" style="margin-top: 15px;">
        <div class="ctrl-slider-header">
          <label>Лимит нагрева (Авто)</label>
          <span class="slider-val" id="auto-temp-limit-val">60.0 °C</span>
        </div>
        <input type="range" id="auto-temp-limit" min="40" max="85" step="1" value="60">
      </div>
    `;

    this._autoStartSelect = this._autoSettingsPanel.querySelector("#auto-start-hour");
    this._autoEndSelect = this._autoSettingsPanel.querySelector("#auto-end-hour");
    this._autoTempLimitSlider = this._autoSettingsPanel.querySelector("#auto-temp-limit");
    this._autoTempLimitVal = this._autoSettingsPanel.querySelector("#auto-temp-limit-val");

    this._autoStartSelect.addEventListener("change", () => {
      const val = parseFloat(this._autoStartSelect.value);
      this._hass.callService("number", "set_value", {
        entity_id: this._entities.heating_start_hour || "number.ems_boiler_heating_start_hour",
        value: val
      });
    });

    this._autoEndSelect.addEventListener("change", () => {
      const val = parseFloat(this._autoEndSelect.value);
      this._hass.callService("number", "set_value", {
        entity_id: this._entities.heating_end_hour || "number.ems_boiler_heating_end_hour",
        value: val
      });
    });

    this._autoTempLimitSlider.addEventListener("change", () => {
      const val = parseFloat(this._autoTempLimitSlider.value);
      this._hass.callService("number", "set_value", {
        entity_id: this._entities.boiler_auto_temp_limit || "number.ems_boiler_auto_temp_limit",
        value: val
      });
    });

    this._autoTempLimitSlider.addEventListener("input", () => {
      this._autoTempLimitVal.textContent = parseFloat(this._autoTempLimitSlider.value).toFixed(1) + " °C";
    });

    // Auto hint
    this._autoHint = document.createElement("div");
    this._autoHint.className = "auto-hint";
    this._autoHint.innerHTML = `<ha-icon icon="mdi:robot-outline"></ha-icon> Управление автоматическое`;

    this._statusContent.appendChild(this._controls);
    this._statusContent.appendChild(this._autoSettingsPanel);
    this._statusContent.appendChild(this._autoHint);
    card.appendChild(this._statusContent);

    // Schedule Tab Content
    this._scheduleContent = document.createElement("div");
    this._scheduleContent.className = "tab-content-schedule hidden";
    this._scheduleContent.innerHTML = `
      <div id="sched-hdr-today" class="schedule-day-header hidden">Сегодня</div>
      <div class="schedule-grid hidden" id="sched-grid-today"></div>
      <div id="sched-hdr-tomorrow" class="schedule-day-header hidden">Завтра</div>
      <div class="schedule-grid hidden" id="sched-grid-tomorrow"></div>
    `;

    // Calc info footer for schedule tab
    this._schedCalcInfo = document.createElement("div");
    this._schedCalcInfo.className = "sched-calc-info";
    this._schedCalcInfo.innerHTML = `<ha-icon icon="mdi:clock-check-outline"></ha-icon><span id="sched-calc-time">–</span>`;
    this._scheduleContent.appendChild(this._schedCalcInfo);

    card.appendChild(this._scheduleContent);

    // Modal Dialog overlay
    this._modalOverlay = document.createElement("div");
    this._modalOverlay.className = "modal-overlay";
    this._modalOverlay.innerHTML = `
      <div class="modal-window">
        <div class="modal-header">
          <span class="modal-title" id="modal-title">Детали часа</span>
          <button class="modal-close" id="modal-close-btn">
            <ha-icon icon="mdi:close"></ha-icon>
          </button>
        </div>
        <div class="modal-content" id="modal-content"></div>
      </div>
    `;
    card.appendChild(this._modalOverlay);

    // Close modal event listeners
    this._modalOverlay.querySelector("#modal-close-btn").addEventListener("click", () => {
      this._modalOverlay.classList.remove("open");
    });
    this._modalOverlay.addEventListener("click", (e) => {
      if (e.target === this._modalOverlay) {
        this._modalOverlay.classList.remove("open");
      }
    });

    // Tab switching event listeners
    const tabStatus = tabsContainer.querySelector("#tab-status");
    const tabSchedule = tabsContainer.querySelector("#tab-schedule");

    tabStatus.addEventListener("click", () => {
      tabStatus.classList.add("active");
      tabSchedule.classList.remove("active");
      this._statusContent.classList.remove("hidden");
      this._scheduleContent.classList.add("hidden");
    });

    tabSchedule.addEventListener("click", () => {
      tabSchedule.classList.add("active");
      tabStatus.classList.remove("active");
      this._statusContent.classList.add("hidden");
      this._scheduleContent.classList.remove("hidden");
      if (this._scheduleNeedsUpdate) {
        this._updateSchedule();
      }
    });

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
    const hwPumpS = e.hw_pump ? st[e.hw_pump] : null;
    const hwTempS = e.hw_return_temp ? st[e.hw_return_temp] : null;
    const dpS    = st["sensor.boiler_dp"];
    const autoTempLimitS = e.boiler_auto_temp_limit ? st[e.boiler_auto_temp_limit] : st["number.ems_boiler_auto_temp_limit"];

    // Cheap dirty check — skip re-paint if nothing changed
    const key = [
      gasS?.state, gasS?.attributes?.current_temperature,
      elecS?.state, powS?.state, tmpS?.state,
      pumpS?.state, valveS?.state, modeS?.state,
      hwPumpS?.state, hwTempS?.state,
      dpS?.state, dpS?.attributes?.schedule?.length,
      dpS?.attributes?.gas_heating_delayed,
      autoTempLimitS?.state
    ].join("|");
    if (key === this._prevKey) return;
    this._prevKey = key;

    const vacBanner = this.shadowRoot.getElementById("vacation-banner");
    const isVacation = dpS?.attributes?.vacation_mode === true;
    if (vacBanner) {
      if (isVacation) {
        vacBanner.classList.remove("hidden");
      } else {
        vacBanner.classList.add("hidden");
      }
    }

    // ── Gas tile ──────────────────────────────────────────────────────────
    const gasTemp   = gasS?.attributes?.current_temperature ?? gasS?.state ?? "–";
    const gasTarget = gasS?.attributes?.temperature ?? "–";
    const gasActive = gasS?.attributes?.hvac_action === "heating" || 
                      (gasS?.state === "heat" && gasTemp !== null && gasTarget !== null && +gasTemp < +gasTarget);
    const gasCls = gasActive ? "st-heating" : "st-idle";
    this._setTile(this._tGas, gasCls, fmt1(gasTemp) + " °C", "цель: " + fmt1(gasTarget) + " °C", gasTemp);

    // ── Elec tile ─────────────────────────────────────────────────────────
    const elecTemp = fmt1(tmpS?.state);
    const elecPowVal = (powS?.state != null && !isNaN(+powS.state)) ? Math.round(+powS.state) : "–";
    const elecActive = elecS?.state === "on";
    const elecCls  = elecActive ? "st-heating" : "st-idle";
    this._setTile(this._tElec, elecCls, elecTemp + " °C", "мощность: " + elecPowVal + " Вт", tmpS?.state);

    // ── Pump & Valve Schema Elements ──────────────────────────────────────
    const pumpOn   = stateOn(pumpS);
    const elecConn = stateOn(valveS);

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

    // ── Update HW Circulation Line (right to left) ──
    if (e.hw_pump) {
      this._schemaHwPump.classList.remove("hidden");
      this._lineHwCirc.classList.remove("hidden");
      this._btnHwPump.classList.remove("hidden");

      const hwPumpOn = stateOn(hwPumpS);
      if (hwPumpOn) {
        this._schemaHwPump.className = "hw-pump-node on";
        this._lineHwCirc.className.baseVal = "flow-line active-reverse";
      } else {
        this._schemaHwPump.className = "hw-pump-node";
        this._lineHwCirc.className.baseVal = "flow-line";
      }

      if (e.hw_return_temp) {
        this._schemaHwTemp.classList.remove("hidden");
        const tVal = fmt1(hwTempS?.state);
        this._schemaHwTemp.textContent = tVal + " °C";
        this._schemaHwTemp.style.color = getTempColor(hwTempS?.state);
      } else {
        this._schemaHwTemp.classList.add("hidden");
      }
    } else {
      this._schemaHwPump.classList.add("hidden");
      this._lineHwCirc.classList.add("hidden");
      this._schemaHwTemp.classList.add("hidden");
      this._btnHwPump.classList.add("hidden");
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
    this._autoSettingsPanel.classList.toggle("hidden", isManual);
    this._autoHint.classList.toggle("hidden",  isManual);

    if (!isManual) {
      const gasDelayed = dpS?.attributes?.gas_heating_delayed === true;
      if (gasDelayed) {
        this._autoHint.innerHTML = `<ha-icon icon="mdi:clock-outline" style="color: var(--warning-color, #ff9800);"></ha-icon> <span style="color: var(--warning-color, #ff9800); font-weight: bold;">Отсрочка газа (выработка эл-ва)</span>`;
        this._autoHint.style.opacity = "1";
      } else {
        this._autoHint.innerHTML = `<ha-icon icon="mdi:robot-outline"></ha-icon> Автоуправление`;
        this._autoHint.style.opacity = "";
      }

      const startEnt = st[this._entities.heating_start_hour || "number.ems_boiler_heating_start_hour"];
      const endEnt = st[this._entities.heating_end_hour || "number.ems_boiler_heating_end_hour"];
      
      if (startEnt && startEnt.state !== undefined && startEnt.state !== "unknown" && startEnt.state !== "unavailable") {
        this._autoStartSelect.value = Math.round(parseFloat(startEnt.state));
      }
      if (endEnt && endEnt.state !== undefined && endEnt.state !== "unknown" && endEnt.state !== "unavailable") {
        this._autoEndSelect.value = Math.round(parseFloat(endEnt.state));
      }
      if (autoTempLimitS && autoTempLimitS.state !== undefined && autoTempLimitS.state !== "unknown" && autoTempLimitS.state !== "unavailable") {
        const limitVal = parseFloat(autoTempLimitS.state);
        if (!isNaN(limitVal)) {
          this._autoTempLimitSlider.value = limitVal;
          this._autoTempLimitVal.textContent = limitVal.toFixed(1) + " °C";
        }
      }
    }

    if (isManual) {
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
      this._updateCtrlBtn(this._btnHeater, elecS?.state === "on");
      // Pump button (disabled if valve off)
      this._updateCtrlBtn(this._btnPump, pumpOn);
      this._btnPump.classList.toggle("disabled", !elecConn);
      // Valve button
      this._updateCtrlBtn(this._btnValve, elecConn, elecConn ? "Подключён" : "Изолирован");
      // HW Pump button
      if (e.hw_pump) {
        this._updateCtrlBtn(this._btnHwPump, stateOn(hwPumpS));
      }
    }

    // ── Update Setpoint value ─────────────────────────────────────────────
    let currentSetpoint = "–";
    if (isManual) {
      const manualActive = dpS?.attributes?.manual_heating_active === true;
      if (manualActive) {
        const mSetpoint = dpS?.attributes?.manual_heating_setpoint;
        if (mSetpoint != null) {
          currentSetpoint = parseFloat(mSetpoint).toFixed(1) + " °C";
        }
      } else {
        const sliderVal = this._mhSetpointSlider ? parseFloat(this._mhSetpointSlider.value) : null;
        if (sliderVal != null) {
          currentSetpoint = sliderVal.toFixed(1) + " °C";
        }
      }
    } else {
      const activeMode = dpS?.state?.toUpperCase();
      if (activeMode) {
        if (activeMode.includes("ELEC")) {
          const tMaxElec = dpS?.attributes?.t_max_elec;
          currentSetpoint = (tMaxElec != null ? parseFloat(tMaxElec).toFixed(1) : "–") + " °C";
        } else if (activeMode.includes("GAS")) {
          const tMaxGas = dpS?.attributes?.t_max_gas;
          currentSetpoint = (tMaxGas != null ? parseFloat(tMaxGas).toFixed(1) : "–") + " °C";
        } else if (activeMode === "IDLE" || activeMode === "PUMP_ONLY") {
          const tMin = dpS?.attributes?.t_min;
          currentSetpoint = (tMin != null ? parseFloat(tMin).toFixed(1) : "–") + " °C (IDLE)";
        }
      }
    }
    if (this._setpointVal) {
      this._setpointVal.textContent = currentSetpoint;
    }

    // ── Update real-time System Avg, DHW Outlet, House Flow temperatures ──
    const tGasVal = parseFloat(gasTemp);
    const tElecVal = parseFloat(tmpS?.state);
    const volGas = parseFloat(dpS?.attributes?.vol_gas) || 0;
    const volElec = parseFloat(dpS?.attributes?.vol_elec) || 0;
    const tMinVal = parseFloat(dpS?.attributes?.t_min) || 40.0;

    let tempSys = "–";
    if (!isNaN(tGasVal) && !isNaN(tElecVal)) {
      const totalVol = volGas + volElec;
      const calculatedSys = totalVol > 0 ? (tGasVal * volGas + tElecVal * volElec) / totalVol : (tGasVal + tElecVal) / 2.0;
      tempSys = calculatedSys.toFixed(1) + " °C";
    }

    let tempDhw = "–";
    let rawTempDhw = null;
    if (elecConn) {
      if (!isNaN(tElecVal)) {
        rawTempDhw = tElecVal;
        tempDhw = tElecVal.toFixed(1) + " °C";
      }
    } else {
      if (!isNaN(tGasVal)) {
        rawTempDhw = tGasVal;
        tempDhw = tGasVal.toFixed(1) + " °C";
      }
    }

    let tempFlow = "–";
    if (rawTempDhw !== null && !isNaN(rawTempDhw)) {
      const calculatedFlow = Math.min(tMinVal, rawTempDhw);
      tempFlow = calculatedFlow.toFixed(1) + " °C";
    }

    if (this._sysAvgVal) {
      this._sysAvgVal.textContent = tempSys;
      const parsedSys = parseFloat(tempSys);
      this._sysAvgVal.style.color = isNaN(parsedSys) ? "var(--primary-text-color)" : getTempColor(parsedSys);
    }
    if (this._dhwOutletVal) {
      this._dhwOutletVal.textContent = tempDhw;
      const parsedDhw = parseFloat(tempDhw);
      this._dhwOutletVal.style.color = isNaN(parsedDhw) ? "var(--primary-text-color)" : getTempColor(parsedDhw);
    }
    if (this._houseFlowVal) {
      this._houseFlowVal.textContent = tempFlow;
      const parsedFlow = parseFloat(tempFlow);
      this._houseFlowVal.style.color = isNaN(parsedFlow) ? "var(--primary-text-color)" : getTempColor(parsedFlow);
    }

    // ── Update PV Limits ──────────────────────────────────────────────────
    const baseToday = dpS?.attributes?.stats?.boiler_average_budget_today;
    const surplusToday = dpS?.attributes?.stats?.curtailed_pv_today;
    const usedToday = dpS?.attributes?.stats?.boiler_used_today;
    const remainingToday = dpS?.attributes?.stats?.remaining_pv_today;
    const baseTomorrow = dpS?.attributes?.stats?.boiler_average_budget_tomorrow;
    const surplusTomorrow = dpS?.attributes?.stats?.curtailed_pv_tomorrow;

    const showTodayStats = (baseToday !== undefined && baseToday !== null && baseToday > 0.0) || 
                           (surplusToday !== undefined && surplusToday !== null && surplusToday > 0.0);

    if (this._baseLimitTodayVal) {
      if (showTodayStats && baseToday !== undefined && baseToday !== null) {
        this._baseLimitTodayVal.textContent = `${baseToday.toFixed(1)} кВтч`;
        this._baseLimitTodayVal.parentElement.classList.remove("hidden");
      } else {
        this._baseLimitTodayVal.textContent = "–";
        this._baseLimitTodayVal.parentElement.classList.add("hidden");
      }
    }

    if (this._solarSurplusTodayVal) {
      if (showTodayStats && surplusToday !== undefined && surplusToday !== null) {
        this._solarSurplusTodayVal.textContent = `${surplusToday.toFixed(1)} кВтч`;
        this._solarSurplusTodayVal.parentElement.classList.remove("hidden");
      } else {
        this._solarSurplusTodayVal.textContent = "–";
        this._solarSurplusTodayVal.parentElement.classList.add("hidden");
      }
    }

    if (this._pvUsedTodayVal) {
      if (showTodayStats && usedToday !== undefined && usedToday !== null) {
        this._pvUsedTodayVal.textContent = `${usedToday.toFixed(1)} кВтч`;
        this._pvUsedTodayVal.parentElement.classList.remove("hidden");
      } else {
        this._pvUsedTodayVal.textContent = "–";
        this._pvUsedTodayVal.parentElement.classList.add("hidden");
      }
    }

    if (this._pvRemainingTodayVal) {
      if (showTodayStats && remainingToday !== undefined && remainingToday !== null) {
        this._pvRemainingTodayVal.textContent = `${remainingToday.toFixed(1)} кВтч`;
        this._pvRemainingTodayVal.parentElement.classList.remove("hidden");
      } else {
        this._pvRemainingTodayVal.textContent = "–";
        this._pvRemainingTodayVal.parentElement.classList.add("hidden");
      }
    }

    const showTomorrowStats = (baseTomorrow !== undefined && baseTomorrow !== null && baseTomorrow > 0.0) || 
                              (surplusTomorrow !== undefined && surplusTomorrow !== null && surplusTomorrow > 0.0);

    if (this._baseLimitTomorrowVal) {
      if (showTomorrowStats && baseTomorrow !== undefined && baseTomorrow !== null) {
        this._baseLimitTomorrowVal.textContent = `${baseTomorrow.toFixed(1)} кВтч`;
        this._baseLimitTomorrowVal.parentElement.classList.remove("hidden");
      } else {
        this._baseLimitTomorrowVal.textContent = "–";
        this._baseLimitTomorrowVal.parentElement.classList.add("hidden");
      }
    }

    if (this._solarSurplusTomorrowVal) {
      if (showTomorrowStats && surplusTomorrow !== undefined && surplusTomorrow !== null) {
        this._solarSurplusTomorrowVal.textContent = `${surplusTomorrow.toFixed(1)} кВтч`;
        this._solarSurplusTomorrowVal.parentElement.classList.remove("hidden");
      } else {
        this._solarSurplusTomorrowVal.textContent = "–";
        this._solarSurplusTomorrowVal.parentElement.classList.add("hidden");
      }
    }

    // ── Update Schedule Grid (Lazy update) ────────────────────────────────
    const tabSchedule = this.shadowRoot.querySelector("#tab-schedule");
    const isScheduleActive = tabSchedule && tabSchedule.classList.contains("active");
    if (isScheduleActive) {
      this._updateSchedule();
    } else {
      this._scheduleNeedsUpdate = true;
    }
  }

  _updateSchedule() {
    this._scheduleNeedsUpdate = false;
    if (!this._hass) return;

    // Update calc info footer
    if (this._schedCalcInfo) {
      const dpAttr = this._hass.states["sensor.boiler_dp"]?.attributes;
      const lastCalc = dpAttr?.last_calculation;
      const calcDur  = dpAttr?.calculation_duration;
      let timeStr = "–";
      let durStr  = "";
      if (lastCalc) {
        try {
          const d = new Date(lastCalc);
          const dateStr = d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
          const timeOnly = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
          timeStr = `${dateStr} ${timeOnly}`;
        } catch (e) { timeStr = lastCalc; }
      }
      if (calcDur != null && typeof calcDur === "number") {
        durStr = ` <span class="sched-calc-dur">${Math.round(calcDur * 1000)} ms</span>`;
      }
      const span = this._schedCalcInfo.querySelector("#sched-calc-time");
      if (span) span.innerHTML = timeStr + durStr;
    }

    const dpS = this._hass.states["sensor.boiler_dp"];
    const schedule = dpS?.attributes?.schedule || [];

    const hdrToday = this._scheduleContent.querySelector("#sched-hdr-today");
    const gridToday = this._scheduleContent.querySelector("#sched-grid-today");
    const hdrTomorrow = this._scheduleContent.querySelector("#sched-hdr-tomorrow");
    const gridTomorrow = this._scheduleContent.querySelector("#sched-grid-tomorrow");

    gridToday.innerHTML = "";
    gridTomorrow.innerHTML = "";

    if (schedule.length === 0) {
      hdrToday.classList.add("hidden");
      hdrTomorrow.classList.add("hidden");
      gridTomorrow.classList.add("hidden");
      gridToday.classList.remove("hidden");
      gridToday.innerHTML = `<div style="grid-column: span 4; text-align: center; color: var(--secondary-text-color); font-size: 13px; padding: 20px 0;">Расписание недоступно</div>`;
      return;
    }

    const startHour = new Date().getHours();
    let todayCount = 0;
    let tomorrowCount = 0;

    schedule.forEach((slot, i) => {
      const tile = document.createElement("div");
      const modeName = slot.mode || "IDLE";
      tile.className = `schedule-tile theme-${modeName.toLowerCase()}`;
      
      const hourStr = String(slot.hour).padStart(2, '0') + ":00";
      
      let iconsHtml = "";
      if (modeName === "IDLE") {
        iconsHtml = `<ha-icon icon="mdi:power-sleep"></ha-icon>`;
      } else if (modeName === "GAS") {
        iconsHtml = `<ha-icon icon="mdi:fire"></ha-icon>`;
      } else if (modeName === "GAS_PUMP") {
        iconsHtml = `<ha-icon icon="mdi:fire"></ha-icon><ha-icon icon="mdi:pump" class="pump-icon"></ha-icon>`;
      } else if (modeName === "ELEC") {
        iconsHtml = `<ha-icon icon="mdi:lightning-bolt"></ha-icon>`;
      } else if (modeName === "ELEC_PUMP") {
        iconsHtml = `<ha-icon icon="mdi:lightning-bolt"></ha-icon><ha-icon icon="mdi:pump" class="pump-icon"></ha-icon>`;
      } else if (modeName === "PUMP_ONLY") {
        iconsHtml = `<ha-icon icon="mdi:pump" class="pump-icon"></ha-icon>`;
      }

      tile.innerHTML = `
        <span class="st-hour">${hourStr}</span>
        <div class="st-icons">${iconsHtml}</div>
        <span class="st-mode mode-${modeName.toLowerCase()}">${modeName}</span>
      `;
      tile.addEventListener("click", () => {
        this._showSlotDetails(slot);
      });

      if (startHour + i < 24) {
        gridToday.appendChild(tile);
        todayCount++;
      } else {
        gridTomorrow.appendChild(tile);
        tomorrowCount++;
      }
    });

    if (todayCount > 0) {
      hdrToday.classList.remove("hidden");
      gridToday.classList.remove("hidden");
    } else {
      hdrToday.classList.add("hidden");
      gridToday.classList.add("hidden");
    }

    if (tomorrowCount > 0) {
      hdrTomorrow.classList.remove("hidden");
      gridTomorrow.classList.remove("hidden");
    } else {
      hdrTomorrow.classList.add("hidden");
      gridTomorrow.classList.add("hidden");
    }
  }

  // ── Show Popup Detail Modal ──────────────────────────────────────────────
  _showSlotDetails(slot) {
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

    const volGas = parseFloat(this._hass?.states["sensor.boiler_dp"]?.attributes?.vol_gas) || 0;
    const volElec = parseFloat(this._hass?.states["sensor.boiler_dp"]?.attributes?.vol_elec) || 0;
    const tMinVal = parseFloat(this._hass?.states["sensor.boiler_dp"]?.attributes?.t_min) || 40.0;

    // Function to get temp_sys
    const getSysStart = () => {
      if (slot.temp_sys_start != null) return slot.temp_sys_start;
      const total = volGas + volElec;
      if (slot.temp_gas_start != null && slot.temp_elec_start != null) {
        return total > 0 ? (slot.temp_gas_start * volGas + slot.temp_elec_start * volElec) / total : (slot.temp_gas_start + slot.temp_elec_start) / 2.0;
      }
      return None;
    };
    const getSysEnd = () => {
      if (slot.temp_sys_end != null) return slot.temp_sys_end;
      const total = volGas + volElec;
      if (slot.temp_gas_end != null && slot.temp_elec_end != null) {
        return total > 0 ? (slot.temp_gas_end * volGas + slot.temp_elec_end * volElec) / total : (slot.temp_gas_end + slot.temp_elec_end) / 2.0;
      }
      return None;
    };

    // Function to get temp_dhw
    const getDhwStart = () => {
      if (slot.temp_dhw_start != null) return slot.temp_dhw_start;
      return slot.bypass ? slot.temp_elec_start : slot.temp_gas_start;
    };
    const getDhwEnd = () => {
      if (slot.temp_dhw_end != null) return slot.temp_dhw_end;
      return slot.bypass ? slot.temp_elec_end : slot.temp_gas_end;
    };

    // Function to get temp_flow
    const getFlowStart = () => {
      if (slot.temp_flow_start != null) return slot.temp_flow_start;
      const dhw = getDhwStart();
      return dhw != null ? Math.min(tMinVal, dhw) : null;
    };
    const getFlowEnd = () => {
      if (slot.temp_flow_end != null) return slot.temp_flow_end;
      const dhw = getDhwEnd();
      return dhw != null ? Math.min(tMinVal, dhw) : null;
    };

    const sysStart = getSysStart();
    const sysEnd = getSysEnd();
    const dhwStart = getDhwStart();
    const dhwEnd = getDhwEnd();
    const flowStart = getFlowStart();
    const flowEnd = getFlowEnd();

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
        <span class="m-label">Температура системы</span>
        <span class="m-val" style="color: ${getTempColor(sysEnd)}">${fmt1(sysStart)}°C → ${fmt1(sysEnd)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">ГВС на выходе</span>
        <span class="m-val" style="color: ${getTempColor(dhwEnd)}">${fmt1(dhwStart)}°C → ${fmt1(dhwEnd)}°C</span>
      </div>
      <div class="modal-row-detail">
        <span class="m-label">Подача в дом</span>
        <span class="m-val" style="color: ${getTempColor(flowEnd)}">${fmt1(flowStart)}°C → ${fmt1(flowEnd)}°C</span>
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
    `;
    
    this._modalOverlay.classList.add("open");
  }

  _updateSliderLimits() {
    if (!this._hass || !this._entities) return;
    const dpS = this._hass.states["sensor.boiler_dp"];
    if (!dpS || !dpS.attributes) return;

    const t_min = dpS.attributes.config_t_min ?? dpS.attributes.t_min ?? 45.0;
    const t_max_gas = dpS.attributes.config_max_gas ?? dpS.attributes.t_max_gas ?? 50.0;
    const t_max_elec = dpS.attributes.config_max_elec ?? dpS.attributes.t_max_elec ?? 70.0;

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
    
    if (mode === "PUMP_ONLY") {
      text = "Расход энергии: 0.00 кВт";
    } else if (mode.includes("GAS")) {
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
  _setTile(tile, stClass, value, sub, rawTemp) {
    tile.className = "tile " + stClass;
    const valueEl = tile.querySelector(".t-value");
    valueEl.textContent = value;
    valueEl.style.color = getTempColor(rawTemp);
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

  _openMoreInfo(entityId) {
    if (!entityId) return;
    const event = new CustomEvent("hass-more-info", {
      detail: { entityId },
      bubbles: true,
      composed: true,
    });
    this.dispatchEvent(event);
  }

  // ── WebSocket fetch (once) ───────────────────────────────────────────────
  async _fetchEntities() {
    this._configLoading = true;
    this._renderLoading("Загружаем конфиг из EMS…");

    try {
      const msg = { type: "ems/get_boiler_config" };
      if (this._cardConfig.entry_id) msg.entry_id = this._cardConfig.entry_id;

      const result = await this._hass.connection.sendMessagePromise(msg);

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
