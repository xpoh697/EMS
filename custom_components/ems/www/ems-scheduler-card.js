/**
 * EMS Scheduler Card
 * Adapted for EMS scheduler based on energy-management-dp-card.js
 */

console.info(
  "%c EMS SCHEDULER %c v0.3.0 ",
  "color: white; background: #2196f3; font-weight: bold; border-radius: 4px 0 0 4px; padding: 2px 6px;",
  "color: white; background: #28a745; font-weight: bold; border-radius: 0 4px 4px 0; padding: 2px 6px;"
);

const MODE_COLORS = {
  'grid_charge': '#2196f3',        // Blue (Charging)
  'discharge': '#ff4500',          // Orange-Red (Discharging)
  'self_consume': '#4caf50',       // Green (Self Consume)
  'idle': '#808080',               // Grey (Idle)
  'idel': '#808080',               // Grey (Idle fallback)
  'pv_charge': '#00bcd4',          // Cyan (PV charging battery)
  'paid_import': '#ffb300',        // Amber (Paid Import)
  'solar_export': '#ff8c00',       // Orange (Solar Export)
  'sale_pv': '#4caf50',            // Green (Normal)
  'sale_pv_no_bat': '#ff8c00',     // Orange (Export PV)
  'sale_pv_bat': '#ff4500',        // Orange-Red (Discharge / export bat)
  'buy': '#2196f3',                // Blue (Charge / buy)
  'stop_sale': '#7da882',          // Faded Green (Stop Sale)
  'bat_emergency': '#e91e63',      // Pink (Emergency)
  'no_pv_sale_no_bat': '#607d8b',  // Blue-Grey (Wait)
  'default': '#727272'
};

const MODE_ICONS = {
  'grid_charge': 'mdi:battery-arrow-down',
  'discharge': 'mdi:battery-arrow-up',
  'self_consume': 'mdi:home-lightning-bolt',
  'idle': 'mdi:sleep',
  'idel': 'mdi:sleep',
  'pv_charge': 'mdi:solar-power-variant',
  'paid_import': 'mdi:transmission-tower',
  'solar_export': 'mdi:solar-power',
  'sale_pv': 'mdi:solar-power-variant',
  'sale_pv_no_bat': 'mdi:solar-power',
  'sale_pv_bat': 'mdi:battery-arrow-up',
  'buy': 'mdi:battery-arrow-down',
  'stop_sale': 'mdi:home-lightning-bolt',
  'bat_emergency': 'mdi:alert-octagon',
  'no_pv_sale_no_bat': 'mdi:clock-outline',
  'default': 'mdi:help-circle'
};

const MODE_LABELS = {
  'sale_pv': 'Normal',
  'sale_pv_no_bat': 'Export PV',
  'sale_pv_bat': 'Discharge',
  'buy': 'Charge',
  'stop_sale': 'Stop Sale',
  'bat_emergency': 'Emergency',
  'no_pv_sale_no_bat': 'Wait',
  'idel': 'Idle',
  'idle': 'Idle',
  // keep fallbacks for raw DP modes
  'grid_charge': 'Grid Charge',
  'discharge': 'Discharge',
  'self_consume': 'Self Consume',
  'pv_charge': 'PV Charge',
  'paid_import': 'Paid Import',
  'solar_export': 'Solar Export'
};

function getSocInfo(soc) {
  if (soc === undefined || soc === null) {
    return { icon: 'mdi:battery-unknown', color: 'rgba(255,255,255,0.2)', percent: '' };
  }

  const val = parseFloat(soc);
  let color = '#ff6b6b'; // Coral Red (Low)
  let icon = 'mdi:battery-20';

  if (val >= 75) {
    color = '#66bb6a'; // Fresh Green (High)
  } else if (val >= 60) {
    color = '#a5d6a7'; // Light Green (Medium-High)
  } else if (val >= 40) {
    color = '#ffe082'; // Amber Yellow (Medium)
  } else if (val >= 25) {
    color = '#ffb74d'; // Orange (Medium-Low)
  }

  if (val >= 95) icon = 'mdi:battery';
  else if (val >= 85) icon = 'mdi:battery-90';
  else if (val >= 75) icon = 'mdi:battery-80';
  else if (val >= 65) icon = 'mdi:battery-70';
  else if (val >= 55) icon = 'mdi:battery-60';
  else if (val >= 45) icon = 'mdi:battery-50';
  else if (val >= 35) icon = 'mdi:battery-40';
  else if (val >= 25) icon = 'mdi:battery-30';
  else if (val >= 15) icon = 'mdi:battery-20';
  else icon = 'mdi:battery-10';

  return { icon, color, percent: val.toFixed(0) };
}

class EmsSchedulerCard extends HTMLElement {
  constructor() {
    super();
    this._initialized = false;
    this._activeTab = 'plan';
    this._selectedDay = 'today';
    this._lastStatsKey = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized && this.shadowRoot) {
      this._updateContent();
    } else if (this._initialized) {
      this._updateUI();
    }
  }

  setConfig(config) {
    this._config = config;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
      this._initLayout();
    }
  }

  _resolveConfigValue(key, defaultVal) {
    const raw = this._config ? this._config[key] : undefined;
    return raw !== undefined ? raw : defaultVal;
  }

  _initLayout() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --card-bg: var(--ha-card-background, var(--card-background-color, #1a1a1a));
          --primary-text: var(--primary-text-color, #ffffff);
          --secondary-text: var(--secondary-text-color, #aaaaaa);
          --accent: #03a9f4;
          --font-family: 'Outfit', 'Inter', sans-serif;
          color-scheme: dark;
        }
        ha-card {
          padding: 24px;
          border-radius: 28px;
          background: var(--card-bg);
          box-shadow: 0 12px 48px rgba(0,0,0,0.3);
          font-family: var(--font-family);
          color: var(--primary-text);
          position: relative;
        }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .title { font-size: 1.1rem; font-weight: 800; opacity: 0.8; }
        .status-badge { 
          padding: 4px 10px; 
          border-radius: 10px; 
          font-size: 0.7rem; 
          font-weight: 800; 
          text-transform: uppercase; 
          border: 1px solid rgba(255,255,255,0.1); 
          background: rgba(255,255,255,0.02);
        }

        .stats-panel {
          background: rgba(255,255,255,0.02);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 24px;
          padding: 16px;
          margin-bottom: 24px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .hero-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }
        
        .hero-badge {
          height: 64px;
          border-radius: 16px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.04);
          transition: all 0.4s ease;
          cursor: pointer;
        }
        .hero-badge:hover { background: rgba(255,255,255,0.08); transform: translateY(-2px); }
        .hero-val { font-size: 1.6rem; font-weight: 900; line-height: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
        .hero-label { font-size: 0.65rem; font-weight: 800; opacity: 0.5; text-transform: uppercase; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }

        .stats-grid { 
          display: grid; 
          grid-template-columns: repeat(auto-fit, minmax(85px, 1fr)); 
          gap: 8px; 
          width: 100%; 
        }
        .stat-card { 
          background: rgba(255,255,255,0.02); 
          padding: 8px 10px; 
          border-radius: 12px; 
          border: 1px solid rgba(255,255,255,0.03); 
          text-align: center; 
          cursor: pointer; 
          transition: all 0.2s; 
        }
        .stat-card:hover { background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.2); }
        .stat-card:active { transform: scale(0.96); }
        .stat-label { font-size: 0.55rem; font-weight: 800; color: var(--secondary-text); text-transform: uppercase; margin-bottom: 2px; display: block; opacity: 0.7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; pointer-events: none; }
        .stat-value { font-size: 0.9rem; font-weight: 800; color: white; line-height: 1.1; pointer-events: none; }

        .section-header { font-size: 0.8rem; font-weight: 900; color: #4dabf5; margin: 12px 0 6px; letter-spacing: 0.05em; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 3px; }
        .timeline-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(70px, 1fr)); gap: 4px; margin-bottom: 6px; }
        
        .hour-bar {
          border-radius: 8px;
          padding: 0;
          aspect-ratio: 1 / 1;
          cursor: pointer;
          position: relative;
          background: transparent;
          overflow: visible;
        }
        .bar-content {
          padding: 2px 1px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          height: 100%;
          width: 100%;
          border-radius: 14px;
          border: 1px solid transparent;
          text-shadow: 0 1px 2px rgba(0,0,0,0.5);
          transition: transform 0.2s ease, box-shadow 0.2s ease, filter 0.2s ease;
          box-sizing: border-box;
        }
        .hour-bar:hover .bar-content {
          transform: scale(1.05);
          box-shadow: 0 12px 30px rgba(0,0,0,0.7);
          filter: brightness(1.3);
          z-index: 10;
          border-color: rgba(255,255,255,0.4);
        }
        .hour-bar.active .bar-content {
          border-width: 2px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }

        .hour-bar.manual-glow .bar-content {
          border-style: solid !important;
          border-width: 2px !important;
          box-shadow: inset 0 0 12px rgba(255, 255, 255, 0.3) !important;
        }

        .manual-indicator {
          position: absolute;
          top: 3px;
          right: 5px;
          color: #ffffff;
          --mdc-icon-size: 14px;
          z-index: 20;
          pointer-events: none;
        }
        .h-icon { --mdc-icon-size: 18px; margin-top: 5px; margin-bottom: 1px; }
        .h-time { font-size: 0.85rem; font-weight: 900; color: white; line-height: 1; }
        .h-prices { display: flex; gap: 4px; margin: 2px 0; }
        .price-buy { font-size: 0.6rem; font-weight: 800; color: #90caf9; }
        .price-sell { font-size: 0.6rem; font-weight: 800; color: #a5d6a7; }
        .h-mode { font-size: 0.55rem; font-weight: 800; text-align: center; line-height: 1; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.02em; }
        .h-soc-top-left {
          position: absolute;
          top: 2px;
          left: 4px;
          display: flex;
          align-items: center;
          gap: 1.5px;
          line-height: 1;
          pointer-events: none;
        }
        .h-soc-top-left ha-icon {
          --mdc-icon-size: 11px;
          margin-bottom: 0.5px;
        }
        .h-soc-percent {
          font-size: 0.55rem;
          font-weight: 900;
          letter-spacing: -0.02em;
        }

        .btn {
          height: 52px;
          background: rgba(255,255,255,0.06);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 16px;
          font-size: 0.85rem;
          font-weight: 800;
          cursor: pointer;
          transition: all 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          color: white;
          white-space: nowrap;
        }
        .btn:hover { background: var(--accent); border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 4px 12px rgba(3, 169, 244, 0.3); }
        .btn.active { background: var(--accent); border-color: var(--accent); box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }
        .btn ha-icon { --mdc-icon-size: 20px; }

        /* Modal Styles */
        .modal-overlay {
          position: fixed; top: 0; left: 0; width: 100%; height: 100%;
          background: rgba(0,0,0,0.7); backdrop-filter: blur(8px);
          display: none; align-items: center; justify-content: center; z-index: 1000;
        }
        .modal-overlay.open { display: flex; }
        .modal-card {
          background: #1e1e1e; width: 95%; max-width: 380px;
          border-radius: 32px; padding: 32px; border: 1px solid rgba(255,255,255,0.1);
          box-shadow: 0 30px 80px rgba(0,0,0,0.6);
          color: white;
        }
        .modal-header { font-size: 1.4rem; font-weight: 900; margin-bottom: 28px; display: flex; justify-content: space-between; align-items: center; }
        .modal-close { cursor: pointer; opacity: 0.6; transition: opacity 0.2s; }
        .modal-close:hover { opacity: 1; }
        .modal-body { display: flex; flex-direction: column; gap: 24px; }
        .form-group { display: flex; flex-direction: column; gap: 10px; }
        .form-label { font-size: 0.8rem; font-weight: 900; color: #4dabf5; text-transform: uppercase; letter-spacing: 0.05em; }
        .modal-info-grid {
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 24px;
          padding: 20px;
          margin-bottom: 20px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          box-shadow: inset 0 1px 1px rgba(255,255,255,0.1), 0 8px 32px rgba(0,0,0,0.2);
        }
        .info-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 0.95rem;
          color: rgba(255, 255, 255, 0.7);
          padding-bottom: 10px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        .info-row:last-child {
          border-bottom: none;
          padding-bottom: 0;
        }
        .info-label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.75rem;
          color: rgba(255, 255, 255, 0.45);
          font-weight: 800;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .info-icon {
          --mdc-icon-size: 18px;
          color: #90caf9;
        }
        .info-icon.solar-color { color: #ffe082; }
        .info-icon.power-color { color: #f48fb1; }
        .info-icon.battery-color { color: #a5d6a7; }
        
        .info-value {
          color: #fff;
          font-family: 'Roboto Mono', monospace;
          font-size: 0.95rem;
          font-weight: 600;
        }
        .color-buy { color: #ff6b6b; font-weight: 700; }
        .color-sell { color: #66bb6a; font-weight: 700; }
        .color-gen { color: #ffe082; font-weight: 700; }
        .color-load { color: #ff6b6b; font-weight: 700; }
        .divider { color: rgba(255, 255, 255, 0.25); font-weight: 300; }
        .unit-text { color: rgba(255, 255, 255, 0.45); font-size: 0.85rem; font-weight: 400; }
        
        .soc-badge {
          background: rgba(76, 175, 80, 0.15);
          border: 1px solid rgba(76, 175, 80, 0.3);
          padding: 4px 10px;
          border-radius: 12px;
          box-shadow: 0 0 10px rgba(76, 175, 80, 0.15);
        }
        .soc-badge b {
          color: #81c784;
          font-family: 'Roboto Mono', monospace;
          font-size: 0.95rem;
          font-weight: 700;
        }
        
        .reason-box {
          background: rgba(255, 255, 255, 0.02);
          border-left: 3px solid #03a9f4;
          border-radius: 6px;
          padding: 10px 12px;
          margin-top: 4px;
          display: flex;
          align-items: flex-start;
          gap: 10px;
          font-size: 0.8rem;
          color: rgba(255, 255, 255, 0.65);
          line-height: 1.35;
        }
        .reason-icon {
          --mdc-icon-size: 16px;
          color: #03a9f4;
          flex-shrink: 0;
          margin-top: 1px;
        }
        
        select {
          background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15);
          border-radius: 16px; padding: 14px; color: white; font-family: inherit; font-size: 1.1rem;
          cursor: pointer; outline: none; appearance: none;
          background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22white%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E");
          background-repeat: no-repeat; background-position: right 14px center; background-size: 18px;
        }
        select option { background: #2a2a2a; color: white; padding: 10px; }

        .modal-footer { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 32px; }
        .btn-save { background: #03a9f4; color: white; border: none; box-shadow: 0 4px 15px rgba(3,169,244,0.3); }
        .btn-clear { background: rgba(255,255,255,0.05); color: #ff5252; border: 1px solid rgba(255,82,82,0.2); }
        .btn:active { transform: scale(0.95); }
        .version-tag { position: absolute; bottom: 4px; right: 8px; font-size: 0.5rem; opacity: 0.3; color: var(--secondary-text); pointer-events: none; }

        /* SOC Slider styles */
        .soc-slider-group {
          display: none;
          flex-direction: column;
          gap: 10px;
          margin-top: 4px;
          padding: 14px 16px;
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 16px;
          animation: fadeIn 0.2s ease;
        }
        .soc-slider-group.visible { display: flex; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
        .soc-slider-label-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .soc-slider-val {
          font-size: 1.1rem;
          font-weight: 900;
          color: #03a9f4;
          font-family: 'Roboto Mono', monospace;
        }
        input[type=range] {
          width: 100%;
          accent-color: #03a9f4;
          cursor: pointer;
          height: 6px;
        }

        /* Tabs */
        .tabs-row {
          display: flex;
          gap: 0;
          margin-bottom: 20px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
          padding-bottom: 0;
        }
        .tab-btn {
          background: transparent;
          border: none;
          color: var(--secondary-text);
          font-family: var(--font-family);
          font-size: 0.95rem;
          font-weight: 800;
          padding: 8px 20px;
          cursor: pointer;
          position: relative;
          transition: color 0.25s ease;
          letter-spacing: 0.02em;
        }
        .tab-btn.active {
          color: #03a9f4;
        }
        .tab-btn.active::after {
          content: '';
          position: absolute;
          bottom: -1px;
          left: 0;
          width: 100%;
          height: 2px;
          background: #03a9f4;
          box-shadow: 0 0 10px rgba(3, 169, 244, 0.8);
          border-radius: 2px 2px 0 0;
        }
        .hidden { display: none !important; }

        /* Stats View */
        .stats-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 14px;
        }
        .stats-title {
          font-size: 0.85rem;
          font-weight: 900;
          color: #4dabf5;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .day-select {
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 12px;
          padding: 6px 32px 6px 12px;
          color: var(--primary-text);
          font-family: var(--font-family);
          font-size: 0.78rem;
          font-weight: 700;
          cursor: pointer;
          outline: none;
          appearance: none;
          background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22white%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E");
          background-repeat: no-repeat;
          background-position: right 8px center;
          background-size: 14px;
          transition: border-color 0.2s;
        }
        .day-select:hover { border-color: rgba(255, 255, 255, 0.25); }
        .day-select option { background: #1e1e1e; color: white; }
        .chart-container {
          position: relative;
          background: rgba(255, 255, 255, 0.02);
          border: 1px solid rgba(255, 255, 255, 0.05);
          border-radius: 20px;
          padding: 16px 12px 8px 4px;
        }
        .chart-legend {
          display: flex;
          gap: 16px;
          justify-content: center;
          margin-top: 8px;
        }
        .legend-item {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 0.72rem;
          font-weight: 700;
          color: rgba(255,255,255,0.6);
        }
        .legend-color {
          width: 16px;
          height: 6px;
          border-radius: 3px;
        }
        .chart-tooltip {
          position: absolute;
          background: rgba(22, 22, 30, 0.97);
          border: 1px solid rgba(255, 255, 255, 0.12);
          border-radius: 12px;
          padding: 10px 14px;
          pointer-events: none;
          opacity: 0;
          transition: opacity 0.15s ease;
          z-index: 100;
          font-size: 0.75rem;
          color: white;
          box-shadow: 0 4px 24px rgba(0,0,0,0.6);
          backdrop-filter: blur(6px);
          min-width: 150px;
        }
        .tooltip-hour {
          font-weight: 900;
          margin-bottom: 5px;
          padding-bottom: 4px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
          color: #03a9f4;
          font-size: 0.8rem;
        }
        .tooltip-row {
          display: flex;
          justify-content: space-between;
          gap: 10px;
          margin: 3px 0;
        }
        .tooltip-label { opacity: 0.55; font-weight: 600; }
        .tooltip-val { font-weight: 800; font-family: 'Roboto Mono', monospace; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">EMS Scheduler</div>
          <div id="status-badge" class="status-badge">AI Operational</div>
        </div>

        <div class="tabs-row">
          <button class="tab-btn active" id="tab-plan-btn">Plan</button>
          <button class="tab-btn" id="tab-stats-btn">Statistics</button>
        </div>

        <!-- Plan View -->
        <div id="plan-view">
          <div class="stats-panel">
            <div class="hero-row">
              <div id="soc-hero" class="hero-badge" onclick="this.getRootNode().host._handleMoreInfo()">
                <span id="soc-val" class="hero-val">--</span>
                <span class="hero-label">Battery SOC</span>
              </div>
              <div id="profit-hero" class="hero-badge" onclick="this.getRootNode().host._handleMoreInfo(this.getAttribute('data-entity'))">
                <span id="profit-val" class="hero-val">--</span>
                <span id="profit-label" class="hero-label">Est. Value</span>
              </div>
            </div>
            <div class="stats-grid" id="stats-container">
              <div class="stat-card" id="dp-advice-card" onclick="this.getRootNode().host._handleMoreInfo('sensor.dp')">
                <span class="stat-label">Degradation / Value</span>
                <div class="stat-value" id="proj-morning">--</div>
              </div>
            </div>
          </div>
          <div id="timeline-container"></div>
        </div>

        <!-- Statistics View -->
        <div id="stats-view" class="hidden">
          <div class="stats-header">
            <span class="stats-title">Load Consumption Profile</span>
            <select id="day-select" class="day-select">
              <option value="today">Today / Avg Today</option>
              <option value="monday">Monday Average</option>
              <option value="tuesday">Tuesday Average</option>
              <option value="wednesday">Wednesday Average</option>
              <option value="thursday">Thursday Average</option>
              <option value="friday">Friday Average</option>
              <option value="saturday">Saturday Average</option>
              <option value="sunday">Sunday Average</option>
            </select>
          </div>
          <div class="chart-container">
            <div id="chart-tooltip" class="chart-tooltip"></div>
            <div id="chart-svg-container"></div>
            <div class="chart-legend">
              <div class="legend-item">
                <div class="legend-color" style="background: linear-gradient(to bottom, #03a9f4, rgba(3,169,244,0.1));"></div>
                <span>Actual Today</span>
              </div>
              <div class="legend-item">
                <div class="legend-color" style="background: #ffe082;"></div>
                <span>Average Profile</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Hourly Modal -->
        <div id="modal" class="modal-overlay">
          <div class="modal-card">
            <div class="modal-header">
              <span id="modal-title">Edit Hour</span>
              <span class="modal-close" onclick="this.getRootNode().host._closeModal()"><ha-icon icon="mdi:close"></ha-icon></span>
            </div>
            <div class="modal-body">
              <div class="modal-info-grid">
                <div class="info-row">
                  <span class="info-label">
                    <ha-icon icon="mdi:swap-horizontal" class="info-icon"></ha-icon>
                    <span>Buy / Sell</span>
                  </span>
                  <b class="info-value">
                    <span id="info-buy" class="color-buy">-</span>
                    <span class="divider"> / </span>
                    <span id="info-sell" class="color-sell">-</span>
                    <span id="info-currency" class="unit-text"></span>
                  </b>
                </div>
                <div class="info-row">
                  <span class="info-label">
                    <ha-icon icon="mdi:lightning-bolt" class="info-icon solar-color"></ha-icon>
                    <span>Gen / Load</span>
                  </span>
                  <b class="info-value">
                    <span id="info-gen" class="color-gen">-</span>
                    <span class="divider"> / </span>
                    <span id="info-load" class="color-load">-</span>
                    <span class="unit-text"> kWh</span>
                  </b>
                </div>
                <div class="info-row" id="info-power-row">
                  <span class="info-label">
                    <ha-icon icon="mdi:flash-outline" class="info-icon power-color"></ha-icon>
                    <span>Power / Amps</span>
                  </span>
                  <b id="info-power" class="info-value">-</b>
                </div>
                <div class="info-row">
                  <span class="info-label">
                    <ha-icon icon="mdi:battery-80" class="info-icon battery-color"></ha-icon>
                    <span>SOC Forecast</span>
                  </span>
                  <span class="soc-badge"><b id="info-forecast-soc">-</b></span>
                </div>
                <div class="reason-box">
                  <ha-icon icon="mdi:information-outline" class="reason-icon"></ha-icon>
                  <span id="info-reason">-</span>
                </div>
              </div>
              <div class="form-group">
                <span class="form-label">Mode Override</span>
                <select id="modal-mode" onchange="this.getRootNode().host._onModeChange(this.value)">
                  <option value="grid_charge">Grid Charge</option>
                  <option value="discharge">Discharge</option>
                  <option value="self_consume">Self Consume</option>
                  <option value="idle">Idle</option>
                  <option value="sale_pv">Normal</option>
                  <option value="sale_pv_no_bat">PV Export</option>
                  <option value="stop_sale">Stop Sale</option>
                  <option value="no_pv_sale_no_bat">Wait</option>
                </select>
                <div class="soc-slider-group" id="soc-slider-group">
                  <div class="soc-slider-label-row">
                    <span class="form-label" style="margin:0">Target SOC</span>
                    <span class="soc-slider-val" id="soc-slider-val">80%</span>
                  </div>
                  <input type="range" id="soc-slider" min="20" max="100" step="0.5" value="80"
                    oninput="this.getRootNode().host._onSocSliderInput(this.value)">
                </div>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-clear" onclick="this.getRootNode().host._clearOverride()">Reset to AI</button>
              <button class="btn btn-save" onclick="this.getRootNode().host._saveOverride()">Save Changes</button>
            </div>
          </div>
        </div>
        <div id="v-tag" class="version-tag">v0.3.0</div>
      </ha-card>
    `;

    // Tab switching
    const planBtn = this.shadowRoot.getElementById('tab-plan-btn');
    const statsBtn = this.shadowRoot.getElementById('tab-stats-btn');
    const planView = this.shadowRoot.getElementById('plan-view');
    const statsView = this.shadowRoot.getElementById('stats-view');

    if (planBtn && statsBtn && planView && statsView) {
      planBtn.addEventListener('click', () => {
        this._activeTab = 'plan';
        planBtn.classList.add('active');
        statsBtn.classList.remove('active');
        planView.classList.remove('hidden');
        statsView.classList.add('hidden');
        this._updateUI();
      });
      statsBtn.addEventListener('click', () => {
        this._activeTab = 'stats';
        statsBtn.classList.add('active');
        planBtn.classList.remove('active');
        statsView.classList.remove('hidden');
        planView.classList.add('hidden');
        this._updateUI();
      });
    }

    // Day dropdown
    const daySelect = this.shadowRoot.getElementById('day-select');
    if (daySelect) {
      daySelect.addEventListener('change', (e) => {
        this._selectedDay = e.target.value;
        // Reset cache key so chart redraws immediately
        const container = this.shadowRoot.getElementById('chart-svg-container');
        if (container) container._lastStatsKey = null;
        this._updateUI();
      });
    }

    this._initialized = true;
  }

  _updateContent() {
    if (!this._hass || !this._config) return;
    this._updateUI();
  }

  _updateUI() {
    const entityId = this._resolveConfigValue('entity', 'sensor.scheduler');
    if (!entityId) return;
    const stateObj = this._hass.states[entityId];
    if (!stateObj) return;

    const attrs = stateObj.attributes;
    const plan = attrs.current_plan || [];
    
    // Get battery SOC
    const soc = attrs.battery_soc !== undefined ? attrs.battery_soc : 0;

    // Update Battery SOC Hero
    const socColor = this._getBatteryColor(soc);
    const socHero = this.shadowRoot.getElementById('soc-hero');
    if (socHero) {
      socHero.style.borderColor = socColor;
      socHero.style.boxShadow = `0 6px 20px ${socColor}22`;
    }
    const socVal = this.shadowRoot.getElementById('soc-val');
    if (socVal) {
      socVal.innerText = Math.round(soc) + '%';
      socVal.style.color = socColor;
    }

    // Profit / Estimated Value Hero
    const profitEntity = this._resolveConfigValue('profit_entity', null);
    const profitHero = this.shadowRoot.getElementById('profit-hero');
    
    let pValRaw = null;
    let pUnit = ' PLN';
    let pLabelText = this._config.profit_label || 'Est. Value';
    
    if (profitEntity && this._hass.states[profitEntity]) {
      const pState = this._hass.states[profitEntity];
      pValRaw = parseFloat(pState.state) || 0;
      pUnit = pState.attributes.unit_of_measurement || '';
    } else {
      const dpState = this._hass.states['sensor.dp'];
      if (dpState && dpState.attributes && dpState.attributes.stats) {
        pValRaw = dpState.attributes.stats.best_value;
      }
    }
    
    if (pValRaw !== null && pValRaw !== undefined) {
      const pColor = pValRaw >= 0 ? '#4caf50' : '#f44336';
      if (socHero) socHero.style.gridColumn = 'span 1';
      if (profitHero) {
        profitHero.style.display = 'flex';
        if (profitEntity) profitHero.setAttribute('data-entity', profitEntity);
        profitHero.style.borderColor = pColor;
        profitHero.style.boxShadow = `0 6px 20px ${pColor}22`;
      }
      const pLabel = this.shadowRoot.getElementById('profit-label');
      if (pLabel) pLabel.innerText = pLabelText;
      const pValEl = this.shadowRoot.getElementById('profit-val');
      if (pValEl) {
        pValEl.innerText = pValRaw.toFixed(2) + pUnit;
        pValEl.style.color = pColor;
      }
    } else if (profitHero) {
      profitHero.style.display = 'none';
      if (socHero) socHero.style.gridColumn = 'span 2';
    }

    const vTag = this.shadowRoot.getElementById('v-tag');
    if (vTag) vTag.innerText = attrs.version || '0.1.1';

    // Degradation/Value card
    const projM = this.shadowRoot.getElementById('proj-morning');
    if (projM) {
      const dpState = this._hass.states['sensor.dp'];
      if (dpState && dpState.attributes && dpState.attributes.stats) {
        const stats = dpState.attributes.stats;
        projM.innerText = (stats.terminal_value_per_kwh || 0).toFixed(4) + ' PLN/kWh';
        projM.style.color = '#03a9f4';
      } else {
        projM.innerText = '--';
        projM.style.color = 'white';
      }
    }

    this._updateExtraIndicators();

    // Status Badge
    const badge = this.shadowRoot.getElementById('status-badge');
    if (badge) {
      const currentVal = stateObj.state;
      const modeLabel = MODE_LABELS[currentVal] || (currentVal ? currentVal.toUpperCase() : 'UNKNOWN');
      badge.innerText = modeLabel;
      const color = MODE_COLORS[currentVal] || MODE_COLORS.default;
      badge.style.color = color;
      badge.style.borderColor = color;
    }

    // Convert current_plan to hourlyData dictionary mapping
    const hourlyData = {};
    plan.forEach(slot => {
      const key = `${slot.date} ${String(slot.hour).padStart(2, '0')}:00`;
      const isManual = attrs.overrides &&
                       attrs.overrides[slot.date] &&
                       (attrs.overrides[slot.date][slot.hour] !== undefined ||
                        attrs.overrides[slot.date][String(slot.hour)] !== undefined);
      const rawOverride = isManual
        ? (attrs.overrides[slot.date][String(slot.hour)] ?? attrs.overrides[slot.date][slot.hour])
        : null;
      // Parse override action part (may be "action:target_soc")
      const overrideAction = rawOverride ? rawOverride.split(':')[0] : null;
      hourlyData[key] = {
        mode: overrideAction || slot.physical_mode || slot.action || 'idle',
        buy_price: slot.buy_price,
        sell_price: slot.sell_price,
        gen: slot.pv_kwh,
        load: slot.consumption_kwh,
        soc: slot.soc,
        power: (slot.power_w / 1000).toFixed(2),
        amps: slot.current_a,
        is_manual: isManual
      };
    });

    if (this._activeTab === 'plan') {
      this._renderTimeline(hourlyData);
    } else if (this._activeTab === 'stats') {
      this._drawStatsChart();
    }
  }

  _updateExtraIndicators() {
    const container = this.shadowRoot.getElementById('stats-container');
    if (!container || !this._hass) return;

    const extras = this._config.extra_indicators || [];
    const expectedKeys = new Set(extras.map(item => item.entity));

    // Remove obsolete cards
    Array.from(container.querySelectorAll('.stat-card[data-entity]')).forEach(card => {
      if (card.id === 'dp-advice-card') return;
      if (!expectedKeys.has(card.getAttribute('data-entity'))) {
        card.remove();
      }
    });

    // Add/update cards
    extras.forEach(item => {
      const entityVal = item.entity;
      const stateObj = this._hass.states[entityVal];
      if (!stateObj) return;

      const newLabel = item.name || stateObj.attributes.friendly_name || 'Sensor';
      const newVal = `${stateObj.state} ${stateObj.attributes.unit_of_measurement || ''}`;

      let card = container.querySelector(`.stat-card[data-entity="${entityVal}"]`);
      if (!card) {
        card = document.createElement('div');
        card.className = 'stat-card';
        card.setAttribute('data-entity', entityVal);
        card.onclick = () => this._handleMoreInfo(entityVal);
        card.innerHTML = `<span class="stat-label"></span><div class="stat-value"></div>`;
        container.appendChild(card);
      }

      const label = card.querySelector('.stat-label');
      const value = card.querySelector('.stat-value');

      if (label.innerText !== newLabel) label.innerText = newLabel;
      if (value.innerText !== newVal) value.innerText = newVal;
    });
  }

  _renderTimeline(data) {
    const container = this.shadowRoot.getElementById('timeline-container');
    if (!container) return;

    const sortedKeys = Object.keys(data).sort();
    if (sortedKeys.length === 0) {
      container.innerHTML = '<div style="text-align:center; padding:20px; opacity:0.5;">No schedule data available</div>';
      return;
    }

    const dates = Array.from(new Set(sortedKeys.map(k => k.split(' ')[0]))).sort();
    const todayStr = dates[0];
    const tomorrowStr = dates[1] || '';

    const hexToRgba = (hex, alpha) => {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    };

    const currentKeysStr = sortedKeys.join(',');
    const overridesStr = JSON.stringify(
      Object.fromEntries(sortedKeys.map(k => [k, data[k].mode + (data[k].is_manual ? '*' : '')]))
    );
    const fullCacheKey = currentKeysStr + '|' + overridesStr;
    if (container._lastKeys !== fullCacheKey) {
      let html = '';
      let currentDayLabel = '';
      sortedKeys.forEach((key, idx) => {
        const slotDate = key.split(' ')[0];
        let label = 'TODAY';
        if (slotDate === tomorrowStr) {
          label = 'TOMORROW';
        } else if (slotDate !== todayStr) {
          label = slotDate;
        }

        const hourData = data[key];

        if (label !== currentDayLabel) {
          if (currentDayLabel !== '') html += '</div>';
          html += `<div class="section-header">${label}</div><div class="timeline-grid">`;
          currentDayLabel = label;
        }

        const modeColor = MODE_COLORS[hourData.mode] || MODE_COLORS.default;
        const bgColor = hexToRgba(modeColor, 0.1);
        const isManual = hourData.is_manual;

        const displaySoc = hourData.soc;
        const socInfo = getSocInfo(displaySoc);

        html += `
          <div class="hour-bar ${idx === 0 ? 'active' : ''} ${isManual ? 'manual-glow' : ''}" data-ts="${key}" data-mode="${hourData.mode}" id="hb-${key.replace(/[: ]/g, '-')}">
            ${isManual ? `<ha-icon class="manual-indicator" icon="mdi:hand-back-right"></ha-icon>` : ''}
            <div class="bar-content" style="border-color: ${modeColor}; background-color: ${bgColor};">
              <div class="h-soc-top-left" style="color: ${socInfo.color};">
                <ha-icon icon="${socInfo.icon}"></ha-icon>
                <span class="h-soc-percent">${socInfo.percent ? socInfo.percent + '%' : ''}</span>
              </div>
              <ha-icon class="h-icon" style="color:${modeColor}" icon="${MODE_ICONS[hourData.mode] || MODE_ICONS.default}"></ha-icon>
              <span class="h-time">${key.split(' ')[1]}</span>
              <div class="h-prices">
                <span class="price-buy">${(hourData.buy_price ?? 0).toFixed(2)}</span>
                <span class="price-sell">${(hourData.sell_price ?? 0).toFixed(2)}</span>
              </div>
              <div class="h-mode" style="color:${modeColor}">${MODE_LABELS[hourData.mode] || hourData.mode}</div>
            </div>
          </div>
        `;
      });
      if (html !== '') html += '</div>';
      container.innerHTML = html;
      container._lastKeys = fullCacheKey;

      container.querySelectorAll('.hour-bar').forEach(bar => {
        bar.addEventListener('click', () => this._openModal(bar.getAttribute('data-ts'), bar.getAttribute('data-mode')));
      });
    } else {
      sortedKeys.forEach(key => {
        const hourData = data[key];
        const bar = container.querySelector(`#hb-${key.replace(/[: ]/g, '-')}`);
        if (!bar) return;

        const modeColor = MODE_COLORS[hourData.mode] || MODE_COLORS.default;
        const isManual = hourData.is_manual;
        const content = bar.querySelector('.bar-content');
        if (isManual) {
          bar.classList.add('manual-glow');
          if (!bar.querySelector('.manual-indicator')) {
            const ind = document.createElement('ha-icon');
            ind.className = 'manual-indicator';
            ind.setAttribute('icon', 'mdi:hand-back-right');
            bar.appendChild(ind);
          }
        } else {
          bar.classList.remove('manual-glow');
          const ind = bar.querySelector('.manual-indicator');
          if (ind) ind.remove();
        }

        const icon = bar.querySelector('.h-icon');
        const modeLabel = bar.querySelector('.h-mode');
        const buyPrice = bar.querySelector('.price-buy');
        const sellPrice = bar.querySelector('.price-sell');
        const priceContainer = bar.querySelector('.h-prices');
        const socContainer = bar.querySelector('.h-soc-top-left');

        if (content) {
          content.style.borderColor = modeColor;
          content.style.backgroundColor = hexToRgba(modeColor, 0.1);
        }
        if (icon) {
          icon.style.color = modeColor;
          icon.setAttribute('icon', MODE_ICONS[hourData.mode] || MODE_ICONS.default);
        }
        if (modeLabel) {
          modeLabel.style.color = modeColor;
          modeLabel.innerText = MODE_LABELS[hourData.mode] || hourData.mode;
        }

        if (socContainer) {
          const displaySoc = hourData.soc;
          const socInfo = getSocInfo(displaySoc);
          socContainer.style.color = socInfo.color;
          const socIcon = socContainer.querySelector('ha-icon');
          if (socIcon) socIcon.icon = socInfo.icon;
          const socPercent = socContainer.querySelector('.h-soc-percent');
          if (socPercent) socPercent.innerText = socInfo.percent ? `${socInfo.percent}%` : '';
        }
        if (priceContainer) {
          priceContainer.style.display = 'flex';
          if (buyPrice) buyPrice.innerText = (hourData.buy_price ?? 0).toFixed(2);
          if (sellPrice) sellPrice.innerText = (hourData.sell_price ?? 0).toFixed(2);
        }
        bar.setAttribute('data-mode', hourData.mode);
      });
    }
  }

  _drawStatsChart() {
    if (!this._hass) return;

    const consumptionEntityId = this._resolveConfigValue('consumption_entity', 'sensor.load_consumption');
    const consumptionState = this._hass.states[consumptionEntityId];

    let actual = Array(24).fill(0);
    let average = Array(24).fill(0);

    if (consumptionState && consumptionState.attributes) {
      const attrs = consumptionState.attributes;
      if (Array.isArray(attrs.today)) {
        actual = attrs.today.map(v => parseFloat(v) || 0);
      }
      const avgKey = this._selectedDay === 'today' ? 'average_today' : `average_${this._selectedDay}`;
      const avgData = attrs[avgKey] || attrs.average_today;
      if (Array.isArray(avgData)) {
        average = avgData.map(v => parseFloat(v) || 0);
      }
    }

    // Normalize arrays to exactly 24 entries
    while (actual.length < 24) actual.push(0);
    actual = actual.slice(0, 24);
    while (average.length < 24) average.push(0);
    average = average.slice(0, 24);

    const container = this.shadowRoot.getElementById('chart-svg-container');
    if (!container) return;

    // Cache key guard
    const statsKey = [this._selectedDay, actual.join(','), average.join(',')].join('|');
    if (container._lastStatsKey === statsKey) return;
    container._lastStatsKey = statsKey;

    // Safe max scale: never 0
    const maxVal = Math.max(1.0, ...actual, ...average);

    // Chart dimensions
    const W = 540, H = 260;
    const padL = 42, padR = 12, padT = 18, padB = 38;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const barW = Math.floor(chartW / 24) - 2;
    const barSpacing = Math.floor(chartW / 24);

    // Grid lines + Y labels
    let gridHtml = '';
    const gridCount = 4;
    for (let i = 0; i <= gridCount; i++) {
      const frac = i / gridCount;
      const val = maxVal * frac;
      const y = padT + chartH - chartH * frac;
      gridHtml += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,0.05)" stroke-width="1"/>`;
      gridHtml += `<text x="${padL - 4}" y="${(y + 3).toFixed(1)}" fill="rgba(255,255,255,0.35)" font-size="8" text-anchor="end" font-weight="700">${val.toFixed(1)}</text>`;
    }

    // X axis labels (every 3 hours)
    let xLabelsHtml = '';
    for (let i = 0; i < 24; i += 3) {
      const x = padL + i * barSpacing + barSpacing / 2;
      xLabelsHtml += `<text x="${x.toFixed(1)}" y="${H - padB + 13}" fill="rgba(255,255,255,0.4)" font-size="8" text-anchor="middle" font-weight="700">${String(i).padStart(2,'0')}:00</text>`;
    }

    // Bars (actual today)
    let barsHtml = '<defs><linearGradient id="bar-grad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#03a9f4" stop-opacity="0.85"/><stop offset="100%" stop-color="#03a9f4" stop-opacity="0.08"/></linearGradient></defs>';
    for (let i = 0; i < 24; i++) {
      const val = actual[i];
      const bh = (val / maxVal) * chartH;
      const x = padL + i * barSpacing + (barSpacing - barW) / 2;
      const y = padT + chartH - bh;
      if (bh > 0.5) {
        barsHtml += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW}" height="${bh.toFixed(1)}" fill="url(#bar-grad)" rx="3" ry="3"/>`;
      }
    }

    // Average profile line
    let points = [];
    for (let i = 0; i < 24; i++) {
      const x = padL + i * barSpacing + barSpacing / 2;
      const y = padT + chartH - (average[i] / maxVal) * chartH;
      points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    const linePath = `M ${points.join(' L ')}`;

    // Dot markers on average line
    let dotsHtml = '';
    for (let i = 0; i < 24; i++) {
      const x = padL + i * barSpacing + barSpacing / 2;
      const y = padT + chartH - (average[i] / maxVal) * chartH;
      dotsHtml += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" fill="#ffe082" stroke="rgba(0,0,0,0.5)" stroke-width="1"/>`;
    }

    // Invisible hover zones
    let hoverHtml = '';
    for (let i = 0; i < 24; i++) {
      const x = padL + i * barSpacing;
      const actVal = actual[i].toFixed(2);
      const avgVal = average[i].toFixed(2);
      const hourStr = String(i).padStart(2, '0') + ':00';
      hoverHtml += `<rect class="hov" x="${x.toFixed(1)}" y="${padT}" width="${barSpacing}" height="${chartH}" fill="transparent" data-hour="${hourStr}" data-act="${actVal}" data-avg="${avgVal}" style="cursor:crosshair"/>`;
    }

    container.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;overflow:visible;">
        <!-- Grid -->
        ${gridHtml}
        <!-- X axis -->
        <line x1="${padL}" y1="${padT + chartH}" x2="${W - padR}" y2="${padT + chartH}" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>
        ${xLabelsHtml}
        <!-- Bars -->
        ${barsHtml}
        <!-- Average Line glow -->
        <filter id="line-glow"><feGaussianBlur stdDeviation="2.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        <path d="${linePath}" fill="none" stroke="#ffe082" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" filter="url(#line-glow)"/>
        <!-- Dots -->
        ${dotsHtml}
        <!-- Hover Zones -->
        ${hoverHtml}
      </svg>
    `;

    // Tooltip logic
    const tooltip = this.shadowRoot.getElementById('chart-tooltip');
    if (!tooltip) return;
    const chartContainer = this.shadowRoot.querySelector('.chart-container');

    container.querySelectorAll('.hov').forEach(zone => {
      zone.addEventListener('mousemove', (e) => {
        const hour = zone.getAttribute('data-hour');
        const act = zone.getAttribute('data-act');
        const avg = zone.getAttribute('data-avg');
        tooltip.innerHTML = `
          <div class="tooltip-hour">${hour}</div>
          <div class="tooltip-row">
            <span class="tooltip-label">Actual:</span>
            <span class="tooltip-val" style="color:#03a9f4">${act} kWh</span>
          </div>
          <div class="tooltip-row">
            <span class="tooltip-label">Avg Profile:</span>
            <span class="tooltip-val" style="color:#ffe082">${avg} kWh</span>
          </div>
        `;
        if (chartContainer) {
          const rect = chartContainer.getBoundingClientRect();
          let left = e.clientX - rect.left + 16;
          let top = e.clientY - rect.top - 80;
          if (left + 160 > rect.width) left = e.clientX - rect.left - 170;
          if (top < 0) top = e.clientY - rect.top + 16;
          tooltip.style.left = `${left}px`;
          tooltip.style.top = `${top}px`;
        }
        tooltip.style.opacity = '1';
      });
      zone.addEventListener('mouseleave', () => { tooltip.style.opacity = '0'; });
    });
  }

  _openModal(timestamp, currentMode) {
    const _entityId = this._resolveConfigValue('entity', 'sensor.scheduler');
    if (!_entityId || !this._hass.states[_entityId]) return;
    const attrs = this._hass.states[_entityId].attributes;
    const plan = attrs.current_plan || [];
    
    const parts = timestamp.split(' ');
    const dateStr = parts[0];
    const hourVal = parseInt(parts[1].split(':')[0], 10);
    const slot = plan.find(s => s.date === dateStr && s.hour === hourVal);
    if (!slot) return;

    this._editingTimestamp = timestamp;
    this.shadowRoot.getElementById('modal-title').innerText = timestamp;
    
    const overrides = attrs.overrides || {};
    const isManual = overrides[dateStr] &&
                     (overrides[dateStr][hourVal] !== undefined || overrides[dateStr][String(hourVal)] !== undefined);
    const rawOverride = isManual ?
                       (overrides[dateStr][hourVal] !== undefined ? overrides[dateStr][hourVal] : overrides[dateStr][String(hourVal)]) :
                       null;
    // Parse override: may be "action:target_soc"
    const overrideParts = rawOverride ? rawOverride.split(':', 2) : null;
    const activeOverrideAction = overrideParts ? overrideParts[0] : slot.action;
    const activeOverrideSoc = (overrideParts && overrideParts.length > 1) ? parseFloat(overrideParts[1]) : null;

    this.shadowRoot.getElementById('modal-mode').value = activeOverrideAction;

    // Get min SOC from number.ems_min_bat_soc entity
    const minSocEntity = this._hass.states['number.ems_min_bat_soc'];
    const minSocVal = minSocEntity ? (parseFloat(minSocEntity.state) || 20.0) : 20.0;

    // Pre-set slider
    const slider = this.shadowRoot.getElementById('soc-slider');
    const sliderGroup = this.shadowRoot.getElementById('soc-slider-group');
    const sliderVal = this.shadowRoot.getElementById('soc-slider-val');
    if (slider) {
      slider.min = minSocVal;
      const defaultSoc = activeOverrideSoc !== null ? activeOverrideSoc : 80.0;
      slider.value = Math.max(minSocVal, Math.min(100, defaultSoc));
      if (sliderVal) sliderVal.innerText = parseFloat(slider.value).toFixed(1) + '%';
    }
    // Show/hide slider based on current mode
    if (sliderGroup) {
      if (activeOverrideAction === 'grid_charge' || activeOverrideAction === 'discharge') {
        sliderGroup.classList.add('visible');
      } else {
        sliderGroup.classList.remove('visible');
      }
    }

    const currency = attrs.unit_of_measurement || 'PLN';

    const buyEl = this.shadowRoot.getElementById('info-buy');
    const sellEl = this.shadowRoot.getElementById('info-sell');
    const currEl = this.shadowRoot.getElementById('info-currency');
    if (buyEl) buyEl.innerText = slot.buy_price !== undefined ? slot.buy_price.toFixed(2) : '0.00';
    if (sellEl) sellEl.innerText = slot.sell_price !== undefined ? slot.sell_price.toFixed(2) : '0.00';
    if (currEl) currEl.innerText = ` ${currency}`;

    const genEl = this.shadowRoot.getElementById('info-gen');
    const loadEl = this.shadowRoot.getElementById('info-load');
    if (genEl) genEl.innerText = slot.pv_kwh !== undefined ? slot.pv_kwh.toFixed(2) : '0.00';
    if (loadEl) loadEl.innerText = slot.consumption_kwh !== undefined ? slot.consumption_kwh.toFixed(2) : '0.00';

    this.shadowRoot.getElementById('info-power').innerText = `${((slot.power_w || 0) / 1000).toFixed(2)} kW / ${slot.current_a || 0} A`;
    
    const reasonEl = this.shadowRoot.getElementById('info-reason');
    if (reasonEl) {
      reasonEl.innerText = isManual ? 'Manual Override' : (slot.mapping_reason || 'Optimized by DP Engine');
    }

    const forecastEl = this.shadowRoot.getElementById('info-forecast-soc');
    if (forecastEl) {
      forecastEl.innerText = `${slot.soc !== undefined ? slot.soc.toFixed(1) : '--'}%`;
    }

    this.shadowRoot.getElementById('modal').classList.add('open');
  }

  _closeModal() {
    this.shadowRoot.getElementById('modal').classList.remove('open');
  }

  async _clearOverride() {
    const parts = this._editingTimestamp.split(' ');
    const dateStr = parts[0];
    const hourVal = parseInt(parts[1].split(':')[0], 10);

    try {
      await this._hass.callService('ems', 'clear_manual_override', {
        date: dateStr,
        hour: hourVal
      });
      this._closeModal();
    } catch (e) {
      console.error('[EmsCard] Clear override failed', e);
      alert('Failed to clear override: ' + e.message);
    }
  }

  async _saveOverride() {
    const action = this.shadowRoot.getElementById('modal-mode').value;
    const parts = this._editingTimestamp.split(' ');
    const dateStr = parts[0];
    const hourVal = parseInt(parts[1].split(':')[0], 10);

    const serviceData = {
      date: dateStr,
      hour: hourVal,
      action: action
    };

    // Include target_soc for grid_charge / discharge
    if (action === 'grid_charge' || action === 'discharge') {
      const slider = this.shadowRoot.getElementById('soc-slider');
      if (slider) {
        serviceData.target_soc = parseFloat(slider.value);
      }
    }

    try {
      await this._hass.callService('ems', 'set_manual_override', serviceData);
      this._closeModal();
    } catch (e) {
      console.error('[EmsCard] Set override failed', e);
      alert('Failed to set override: ' + e.message);
    }
  }

  _onModeChange(value) {
    const sliderGroup = this.shadowRoot.getElementById('soc-slider-group');
    if (!sliderGroup) return;
    if (value === 'grid_charge' || value === 'discharge') {
      sliderGroup.classList.add('visible');
    } else {
      sliderGroup.classList.remove('visible');
    }
  }

  _onSocSliderInput(value) {
    const sliderVal = this.shadowRoot.getElementById('soc-slider-val');
    if (sliderVal) sliderVal.innerText = parseFloat(value).toFixed(1) + '%';
  }

  _handleMoreInfo(entityId) {
    const target = entityId || this._config.entity || 'sensor.scheduler';
    const event = new CustomEvent('hass-more-info', {
      detail: { entityId: target },
      bubbles: true,
      composed: true
    });
    this.dispatchEvent(event);
  }

  _getBatteryColor(soc) {
    if (soc < 20) return '#f44336';
    if (soc < 50) return '#ff9800';
    if (soc < 80) return '#ffeb3b';
    return '#4caf50';
  }

  getCardSize() { return 12; }
}

customElements.define('ems-scheduler-card', EmsSchedulerCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "ems-scheduler-card", name: "EMS Scheduler Card", preview: true });
