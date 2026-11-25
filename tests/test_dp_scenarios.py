"""Unit tests for EMS Dynamic Programming (DP) engine scenario testing."""
import os
import sys
import unittest
from datetime import datetime, timezone
from types import ModuleType

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 1. Idempotent bootstrap of mock Home Assistant modules if not present
if "homeassistant" not in sys.modules:
    def _mkmod(name: str) -> ModuleType:
        m = ModuleType(name)
        m.__path__ = []
        sys.modules[name] = m
        return m

    ha = _mkmod("homeassistant")
    ha_core = _mkmod("homeassistant.core")
    ha_core.HomeAssistant = type("HomeAssistant", (), {})
    ha_core.callback = lambda f: f
    
    ha_ce = _mkmod("homeassistant.config_entries")
    ha_ce.ConfigEntry = type("ConfigEntry", (), {})
    
    ha_comp = _mkmod("homeassistant.components")
    ha_comp.frontend = type("frontend", (), {})
    ha_comp.websocket_api = ModuleType("websocket_api")
    ha_comp.websocket_api.websocket_command = lambda *args, **kwargs: (lambda f: f)
    ha_comp.websocket_api.async_response = lambda f: f
    sys.modules["homeassistant.components.websocket_api"] = ha_comp.websocket_api
    
    ha_http = _mkmod("homeassistant.components.http")
    ha_http.HomeAssistantView = type("HomeAssistantView", (), {})
    
    ha_util = _mkmod("homeassistant.util")
    dt = ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime.now(timezone.utc)
    ha_util.dt = dt
    sys.modules["homeassistant.util.dt"] = dt

from custom_components.ems.dp_engine import (
    run_unified_dp,
    DPConfig,
    ACT_SOL,
    ACT_DIS,
    ACT_PV_CHARGE,
    ACT_GRID_CHARGE,
    ACT_SELF_CONSUME,
    ACT_PAID_IMPORT,
)
from custom_components.ems.const import PRICE_MODE_FIXED, PRICE_MODE_SENSOR
from custom_components.ems.utils import resolve_hourly_tariffs


class TestDPScenarios(unittest.TestCase):
    """Test suite covering DP engine operational and tariff scenarios."""

    def setUp(self):
        """Default battery configuration for tests."""
        self.capacity = 16.3  # kWh
        self.min_soc = 12     # %
        self.usable_capacity = self.capacity * ((100.0 - self.min_soc) / 100.0)  # 14.344 kWh
        self.config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=self.min_soc,
            battery_capacity=self.capacity,
            min_energy_to_discharge=0.5,
            disable_discharge=False,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_arbitrage_profit=0.70,
            eta_charge=0.94,
            eta_discharge=0.94,
        )

    def _soc_to_usable(self, soc: float) -> float:
        return self.capacity * max(0.0, (soc - self.min_soc) / 100.0)

    def test_night_to_solar(self):
        """1. Ночь -> Солнце: ночью нет ложного слива, днем PV заряжает АКБ, излишки продаются."""
        # Full 24h horizon: night 00..05 (PV=0), midday 06..16 (strong PV up to 4.0), evening 17..22 (peak buy 1.50)
        slots = []
        for h in range(0, 6):
            slots.append({
                "date": "2026-09-07",
                "hour": h,
                "buy_price": 1.05,
                "sell_price": 0.85,
                "pv_kwh": 0.0,
                "consumption_kwh": 0.4,
                "ev_kwh": 0.0,
                "planned_boiler_kwh": 0.0,
            })
        for h in range(6, 17):
            pvs = [0.2, 1.0, 2.5, 3.5, 4.0, 4.0, 3.5, 2.5, 1.5, 0.8, 0.2]
            slots.append({
                "date": "2026-09-07",
                "hour": h,
                "buy_price": 1.20,
                "sell_price": 0.70,
                "pv_kwh": pvs[h - 6],
                "consumption_kwh": 0.5,
                "ev_kwh": 0.0,
                "planned_boiler_kwh": 0.0,
            })
        for h in range(17, 23):
            slots.append({
                "date": "2026-09-07",
                "hour": h,
                "buy_price": 1.50,
                "sell_price": 1.00,
                "pv_kwh": 0.0,
                "consumption_kwh": 0.6,
                "ev_kwh": 0.0,
                "planned_boiler_kwh": 0.0,
            })

        # Start with empty usable (12% SOC)
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=0.0,
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        # Invariant 1: In night hours (0..5), no false discharge to grid
        night_dis = [d for d in dis if d["hour"] < 6]
        self.assertEqual(len(night_dis), 0, "Battery should not discharge to grid at night with 0 usable")

        # Invariant 2: Midday hours must charge battery from solar
        pvc_hours = [p["hour"] for p in pvc]
        self.assertTrue(any(h in pvc_hours for h in (8, 9, 10, 11, 12, 13)), "PV charging must occur during solar surplus")

        # Invariant 3: Total solar charged must be significant
        total_pvc = sum(p["charge_kwh"] for p in pvc)
        self.assertGreater(total_pvc, 5.0, "Total PV charged energy should exceed 5 kWh across midday")

    def test_cloudy_morning_no_false_export(self):
        """2. Пасмурное утро: при low PV и отсутствии маржи экспорт в сеть строго 0, разрешен только SC."""
        slots = [
            {"date": "2026-09-06", "hour": 22, "buy_price": 1.21, "sell_price": 1.01, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-06", "hour": 23, "buy_price": 1.15, "sell_price": 0.95, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-07", "hour": 0,  "buy_price": 1.07, "sell_price": 0.87, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-07", "hour": 1,  "buy_price": 1.02, "sell_price": 0.82, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-07", "hour": 6,  "buy_price": 1.77, "sell_price": 1.14, "pv_kwh": 0.18, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-07", "hour": 7,  "buy_price": 1.80, "sell_price": 1.17, "pv_kwh": 0.30, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]

        # Battery has 22% SOC at 22:00
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(22.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        # Invariant 1: In morning pre-solar slot 06:00, NO export to grid
        dis_h6 = next((d for d in dis if d["hour"] == 6), None)
        if dis_h6:
            self.assertEqual(dis_h6.get("planned_export_kwh", 0.0), 0.0, "Morning pre-solar export must be 0")

        # Invariant 2: Home deficit in 06:00 is covered by SC
        sc_h6 = next((s for s in sc if s["hour"] == 6), None)
        self.assertIsNotNone(sc_h6, "06:00 should use self-consumption to save 1.77 PLN buy price")
        self.assertGreater(sc_h6["planned_energy_kwh"], 0.0)

        # Invariant 3: 23:00 must self-consume and not sit in IDLE
        sc_h23 = next((s for s in sc if s["hour"] == 23), None)
        self.assertIsNotNone(sc_h23, "23:00 should self-consume available energy instead of idling")

    def test_negative_prices(self):
        """3. Отрицательные цены: buy_price < 0 при PV > 0 вызывает PAID_IMPORT, низкий sell_price подавляет экспорт."""
        slots = [
            # Hour 0: normal
            {"date": "2026-09-07", "hour": 0, "buy_price": 0.80, "sell_price": 0.60, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Hour 1: negative buy price (-0.15 PLN) with daytime PV generation
            {"date": "2026-09-07", "hour": 1, "buy_price": -0.15, "sell_price": -0.25, "pv_kwh": 1.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Hour 2: normal
            {"date": "2026-09-07", "hour": 2, "buy_price": 0.90, "sell_price": 0.70, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]

        from dataclasses import replace
        test_config = replace(self.config, min_arbitrage_profit=0.80)

        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(30.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=test_config,
            remaining_hour_fraction=1.0,
        )

        # Invariant 1: At 30% SOC with negative buy price, DP maximizes earnings via full GRID_CHARGE (getting paid per kWh)
        h1_chg = next((c for c in chg if c["hour"] == 1), None)
        self.assertIsNotNone(h1_chg, "Negative buy price with battery headroom must trigger GRID_CHARGE")
        self.assertAlmostEqual(h1_chg["planned_energy_kwh"], 6.6, delta=0.1)

        # Invariant 2: Sell price < min_sell_price (-0.25 < 0.04) must have 0 export
        h1_dis = next((d for d in dis if d["hour"] == 1), None)
        self.assertIsNone(h1_dis, "Negative sell price must never allow battery discharge export")

        # Invariant 3: When battery is already full (100% SOC), negative buy price + PV must trigger PAID_IMPORT
        _, dis_full, _, _, pim_full, _ = run_unified_dp(
            slots=slots,
            current_usable=self.usable_capacity,
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=test_config,
            remaining_hour_fraction=1.0,
        )
        h1_pim = next((p for p in pim_full if p["hour"] == 1), None)
        self.assertIsNotNone(h1_pim, "Negative buy price with PV and full battery must trigger PAID_IMPORT")
        self.assertEqual(h1_pim["planned_grid_import_kwh"], 0.6)
        self.assertGreater(h1_pim["expected_revenue"], 0.0, "Paid import must yield positive revenue")

    def test_manual_overrides(self):
        """4. Ручной override: Two-pass DP строго уважает оверрайды grid_charge:100 и discharge:15."""
        slots = [
            {"date": "2026-09-07", "hour": 0, "buy_price": 1.0, "sell_price": 0.8, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Override at hour 1: charge to 100%
            {"date": "2026-09-07", "hour": 1, "buy_price": 0.9, "sell_price": 0.7, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0, "override": "grid_charge:100"},
            {"date": "2026-09-07", "hour": 2, "buy_price": 1.1, "sell_price": 0.9, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Override at hour 3: discharge to 15%
            {"date": "2026-09-07", "hour": 3, "buy_price": 1.2, "sell_price": 1.0, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0, "override": "discharge:15"},
        ]

        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(50.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        # Invariant 1: Hour 1 grid_charge:100 override must be scheduled with override=True
        chg_h1 = next((c for c in chg if c["hour"] == 1), None)
        self.assertIsNotNone(chg_h1, "Hour 1 grid_charge override must be present in charge_hours")
        self.assertTrue(chg_h1["override"], "Hour 1 charge entry must have override=True flag")
        self.assertGreater(chg_h1["planned_energy_kwh"], 5.0, "Hour 1 charge must draw substantial energy towards 100%")

        # Invariant 2: Hour 3 discharge:15 override must be scheduled with override=True
        dis_h3 = next((d for d in dis if d["hour"] == 3), None)
        self.assertIsNotNone(dis_h3, "Hour 3 discharge override must be present in discharge_hours")
        self.assertTrue(dis_h3["override"], "Hour 3 discharge entry must have override=True flag")
        self.assertGreater(dis_h3["planned_energy_kwh"], 5.0, "Hour 3 discharge must discharge substantial energy towards 15%")

    def test_manual_override_sale_pv_charges_battery_fully(self):
        """4b. Ручной override Normal (sale_pv): принудительный физический заряд от излишков PV без арбитражного удержания."""
        slots = [
            # Hour 9: override Normal, PV surplus 1.0 kWh
            {"date": "2026-09-10", "hour": 9, "buy_price": 1.25, "sell_price": 0.61, "pv_kwh": 1.5, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0, "override": "sale_pv"},
            # Hour 10: override Normal, PV surplus 2.12 kWh, sell_price is high (0.5219)
            {"date": "2026-09-10", "hour": 10, "buy_price": 0.7217, "sell_price": 0.5219, "pv_kwh": 2.68, "consumption_kwh": 0.56, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0, "override": "sale_pv"},
            # Hour 11: normal AI slot, cheaper sell price
            {"date": "2026-09-10", "hour": 11, "buy_price": 0.59, "sell_price": 0.39, "pv_kwh": 3.5, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]

        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(30.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        pvc_h9 = next((p for p in pvc if p["hour"] == 9), None)
        self.assertIsNotNone(pvc_h9, "Hour 9 must have PV charge scheduled")
        self.assertTrue(pvc_h9.get("override"), "Hour 9 must be flagged as override")
        self.assertAlmostEqual(pvc_h9["charge_kwh"], 1.0, delta=0.15, msg="Hour 9 must absorb full 1.0 kWh surplus")

        pvc_h10 = next((p for p in pvc if p["hour"] == 10), None)
        self.assertIsNotNone(pvc_h10, "Hour 10 must have PV charge scheduled")
        self.assertTrue(pvc_h10.get("override"), "Hour 10 must be flagged as override")
        # Critical assertion: must NOT be reduced to 0.1 kWh due to high sell price!
        self.assertAlmostEqual(pvc_h10["charge_kwh"], 2.1, delta=0.15, msg="Hour 10 must absorb full ~2.1 kWh surplus despite high sell price")

        trajectory = summary.get("expected_trajectory", [])
        # Starting at 30.0%, after 1.0 kWh (+6.1% on 16.3kWh battery) -> ~36% (or ~41% usable)
        # after 2.1 kWh (+12.8%) -> ~49% (or ~54% usable)
        self.assertGreater(trajectory[2], 48.0, f"Expected SOC at 11:00 to exceed 48%, got {trajectory[2]}%")

    def test_soc_boundaries_and_transitions(self):
        """5. Границы SOC: вечерний минимум 17% в 22:00..05:00 и разблокировка утреннего 12% с 06:00."""
        slots = []
        # 21:00 to 07:00
        for h in range(21, 24):
            slots.append({"date": "2026-09-06", "hour": h, "buy_price": 1.2, "sell_price": 1.0, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0})
        for h in range(0, 8):
            slots.append({"date": "2026-09-07", "hour": h, "buy_price": 1.1 if h < 6 else 1.8, "sell_price": 0.9 if h < 6 else 1.1, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0})

        # Start with 25% SOC
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(25.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        trajectory = summary["expected_trajectory"]

        # Invariant 1: In evening hours (slots 0..2, hours 21..23), SOC must NEVER breach evening min 17%
        for idx in range(3):
            soc_val = trajectory[idx]
            self.assertGreaterEqual(
                soc_val, 17.0 - 1e-3,
                f"Slot {idx} (hour {slots[idx]['hour']}) breached evening min SOC 17%: got {soc_val}%"
            )

        # Invariant 2: In morning hours (slots 3..10, hours 00..07), morning min SOC 12% is active
        for idx in range(3, len(trajectory)):
            soc_val = trajectory[idx]
            self.assertGreaterEqual(
                soc_val, 12.0 - 1e-3,
                f"Slot {idx} breached morning min SOC 12%: got {soc_val}%"
            )

        # Invariant 3: SOC never exceeds 100%
        for idx, soc_val in enumerate(trajectory):
            self.assertLessEqual(soc_val, 100.0 + 1e-3, f"Slot {idx} exceeded 100% capacity: got {soc_val}%")


    def test_no_premature_grid_discharge_in_pre_peak_hour(self):
        """7. Удержание IDLE в предпиковый час (18:00=1.68, 19:00=2.59, 20:00=2.43)."""
        slots = [
            {"date": "2026-09-11", "hour": 18, "buy_price": 1.95, "sell_price": 1.68, "pv_kwh": 0.0, "consumption_kwh": 0.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-11", "hour": 19, "buy_price": 2.90, "sell_price": 2.59, "pv_kwh": 0.0, "consumption_kwh": 0.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-11", "hour": 20, "buy_price": 2.75, "sell_price": 2.43, "pv_kwh": 0.0, "consumption_kwh": 0.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-11", "hour": 21, "buy_price": 1.20, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(93.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        # В 18:00 разряда быть не должно (IDLE)
        dis_18 = next((d for d in dis if d["hour"] == 18), None)
        self.assertIsNone(dis_18, "Slot 18:00 must NOT discharge to grid")

        # В 19:00 и 20:00 разряд на пиковых ценах
        dis_19 = next((d for d in dis if d["hour"] == 19), None)
        self.assertIsNotNone(dis_19, "Slot 19:00 must discharge at peak price")
        self.assertGreater(dis_19["planned_energy_kwh"], 6.0)

        dis_20 = next((d for d in dis if d["hour"] == 20), None)
        self.assertIsNotNone(dis_20, "Slot 20:00 must discharge remaining energy")
        self.assertGreater(dis_20["planned_energy_kwh"], 5.0)

    def test_fractional_hour_pv_charge_near_boundary(self):
        """8. Не сбрасывать PV_CHARGE в SOL на последних минутах часа при sub-step излишке солнца."""
        slots = [
            {"date": "2026-09-12", "hour": 10, "buy_price": 0.77, "sell_price": 0.57, "pv_kwh": 2.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 11, "buy_price": 0.63, "sell_price": 0.43, "pv_kwh": 3.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 18, "buy_price": 1.80, "sell_price": 1.20, "pv_kwh": 0.0, "consumption_kwh": 1.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 19, "buy_price": 2.20, "sell_price": 1.80, "pv_kwh": 0.0, "consumption_kwh": 1.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        # Запуск на 58-й минуте (remaining_hour_fraction = 95 / 3600 = 0.0264)
        # Scaled pv_kwh ≈ 0.052, consumption ≈ 0.013 -> pv_surplus ≈ 0.039 kWh (< 0.1 step)
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(32.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=0.0264,
        )
        # Должен сохраниться PV_CHARGE для 10h, а не сброситься в SOL/solar_export
        pvc_10 = next((p for p in pvc if p["hour"] == 10), None)
        self.assertIsNotNone(pvc_10, "Hour 10 must retain PV_CHARGE even at minute 58 (remaining_hour_fraction=0.0264)")

    def test_cheap_daytime_grid_charge_allows_night_discharge(self):
        """9. Дешевая дневная энергия (0.68 PLN) закупается для вечера, не блокируя ночной разряд."""
        slots = [
            # Tonight: moderate prices (1.20 - 1.05 PLN)
            {"date": "2026-09-12", "hour": 21, "buy_price": 1.30, "sell_price": 1.10, "pv_kwh": 0.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 22, "buy_price": 1.20, "sell_price": 1.00, "pv_kwh": 0.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 23, "buy_price": 1.14, "sell_price": 0.94, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Tomorrow daytime: cheap grid buy price (0.68 PLN) with low solar (1.5 kWh)
            {"date": "2026-09-13", "hour": 11, "buy_price": 0.68, "sell_price": 0.48, "pv_kwh": 1.5, "consumption_kwh": 1.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 12, "buy_price": 0.68, "sell_price": 0.48, "pv_kwh": 1.5, "consumption_kwh": 0.7, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Tomorrow evening: peak price (1.44 PLN) with high consumption
            {"date": "2026-09-13", "hour": 20, "buy_price": 1.44, "sell_price": 1.24, "pv_kwh": 0.0, "consumption_kwh": 2.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        # Start at 21:00 with 30% SOC
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(30.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        # Hour 21 discharges to supply home/export, hour 22 self-consumes, not idling on grid!
        dis_or_sc_21 = next((s for s in (dis + sc) if s["hour"] == 21 and s["date"] == "2026-09-12"), None)
        self.assertIsNotNone(dis_or_sc_21, "Hour 21 must discharge/self-consume instead of idling on grid")

        # Tomorrow at 11:00, cheap grid charging (0.68 PLN) is scheduled
        gc_11 = next((s for s in chg if s["hour"] == 11 and s["date"] == "2026-09-13"), None)
        self.assertIsNotNone(gc_11, "Hour 11 tomorrow must grid charge at cheap price (0.68 PLN)")

    def test_night_self_consumption_protected_against_evening_export(self):
        """11. Защита ночного самопотребления: экспорт в сеть не опустошает АКБ, если энергия нужна дому ночью."""
        slots = [
            # Evening peak hours: high export price (1.35), home consumption 1.0 kWh
            {"date": "2026-09-12", "hour": 19, "buy_price": 1.55, "sell_price": 1.35, "pv_kwh": 0.0, "consumption_kwh": 1.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 20, "buy_price": 1.50, "sell_price": 1.30, "pv_kwh": 0.0, "consumption_kwh": 0.8, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Night hours: buy price 1.20 - 1.05 PLN, home consumption 0.6 kWh/h
            {"date": "2026-09-12", "hour": 21, "buy_price": 1.30, "sell_price": 1.00, "pv_kwh": 0.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 22, "buy_price": 1.20, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 23, "buy_price": 1.15, "sell_price": 0.85, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 0, "buy_price": 1.10, "sell_price": 0.80, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 1, "buy_price": 1.05, "sell_price": 0.75, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Morning solar arrives at hour 8
            {"date": "2026-09-13", "hour": 8, "buy_price": 0.80, "sell_price": 0.60, "pv_kwh": 1.5, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        # Start at 19:00 with 60% SOC (around 7.8 kWh usable)
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(60.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        # Night hours 22:00, 23:00, 00:00 must self-consume or discharge to cover home, NOT sit in IDLE!
        h22_act = next((s for s in sc + dis if s["hour"] == 22 and s["date"] == "2026-09-12"), None)
        sc_23 = next((s for s in sc if s["hour"] == 23 and s["date"] == "2026-09-12"), None)
        sc_00 = next((s for s in sc if s["hour"] == 0 and s["date"] == "2026-09-13"), None)
        self.assertIsNotNone(h22_act, "Hour 22 must cover home load, not idle on grid")
        self.assertIsNotNone(sc_23, "Hour 23 must self-consume, not idle on grid")
        self.assertIsNotNone(sc_00, "Hour 00 must self-consume, not idle on grid")

    def test_min_end_usable_in_daytime_grid_charge_prevents_night_idle(self):
        """12. Учет min_end_usable в дневной зарядке: дешевая сеть днем по 0.68 PLN покрывает конечный резерв, исключая ночной IDLE."""
        slots = [
            # Tonight: moderate night prices
            {"date": "2026-09-12", "hour": 22, "buy_price": 1.20, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.6, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 23, "buy_price": 1.15, "sell_price": 0.85, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 2, "buy_price": 1.03, "sell_price": 0.83, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 4, "buy_price": 1.01, "sell_price": 0.81, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 5, "buy_price": 1.03, "sell_price": 0.83, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Tomorrow daytime: cheap grid buy price (0.68 PLN)
            {"date": "2026-09-13", "hour": 11, "buy_price": 0.68, "sell_price": 0.48, "pv_kwh": 1.5, "consumption_kwh": 1.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 13, "buy_price": 0.72, "sell_price": 0.52, "pv_kwh": 2.5, "consumption_kwh": 1.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            # Tomorrow evening: peak home buy price (sell stays below night SC break-even so evening doesn't starve night SC)
            {"date": "2026-09-13", "hour": 20, "buy_price": 1.44, "sell_price": 0.85, "pv_kwh": 0.0, "consumption_kwh": 1.0, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        # Start at 22:00 with 45% SOC, with a required reserve min_end_usable = 3.0 kWh
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(45.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=3.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        # Hours 02:00, 04:00, 05:00 must NOT be in IDLE: they must self-consume!
        sc_h2 = next((s for s in sc if s["hour"] == 2 and s["date"] == "2026-09-13"), None)
        sc_h4 = next((s for s in sc if s["hour"] == 4 and s["date"] == "2026-09-13"), None)
        self.assertIsNotNone(sc_h2, "Hour 02:00 must self-consume, not idle")
        self.assertIsNotNone(sc_h4, "Hour 04:00 must self-consume, not idle")

    def test_dual_min_soc_evening_17_morning_12(self):
        """13. Двойной порог SOC: вечерний лимит 17% удерживается до 00:00, а с 00:00 доразряжается до утреннего минимума 12%."""
        slots = []
        # Evening slots 21..23
        for h in (21, 22, 23):
            slots.append({"date": "2026-09-12", "hour": h, "buy_price": 1.20, "sell_price": 1.00, "pv_kwh": 0.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0})
        # Night slots 00..05
        for h in range(0, 6):
            slots.append({"date": "2026-09-13", "hour": h, "buy_price": 1.05, "sell_price": 0.85, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0})
        # Morning slots 06..08 (before solar)
        for h in (6, 7, 8):
            slots.append({"date": "2026-09-13", "hour": h, "buy_price": 1.05 if h < 8 else 0.80, "sell_price": 0.85 if h < 8 else 0.60, "pv_kwh": 0.0 if h < 8 else 0.5, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0})

        # Start at 21:00 with 30% SOC
        chg, dis, pvc, sc, pim, summary = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(30.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=0.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )

        trajectory = summary["expected_trajectory"]

        # Invariant 1: In evening hours (21h up to 23h), SOC never breaches evening min 17%
        for idx in range(3):
            soc_val = trajectory[idx]
            self.assertGreaterEqual(
                soc_val, 17.0 - 1e-3,
                f"Slot {idx} (hour {slots[idx]['hour']}) breached evening min SOC 17%: got {soc_val}%"
            )

        # Invariant 2: Starting from 00:00 (slots 3+), battery discharges below 17% down towards morning min 12%
        min_night_soc = min(trajectory[3:])
        self.assertLess(min_night_soc, 16.0, f"Night slots starting at 00:00 must discharge below 17%, min reached was {min_night_soc}%")
        self.assertGreaterEqual(min_night_soc, 12.0 - 1e-3, f"Morning min SOC 12% must not be breached: got {min_night_soc}%")

    def test_solar_headroom_reservation_and_subsequent_sale(self):
        """14. Резервирование места под бесплатное солнце и учет последующей дорогой продажи."""
        # Case A: 14:00, SOC 89%, free capacity 2.08 kWh. Upcoming solar at 15:00 is 1.31 kWh with sell_price 0.60 < buy_price 0.74.
        # Cheap solar must NOT be displaced by grid charging.
        slots_cheap_pv = [
            {"date": "2026-09-12", "hour": 14, "buy_price": 0.736, "sell_price": 0.536, "pv_kwh": 2.25, "consumption_kwh": 2.28, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 15, "buy_price": 0.803, "sell_price": 0.603, "pv_kwh": 1.31, "consumption_kwh": 0.36, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 16, "buy_price": 0.903, "sell_price": 0.703, "pv_kwh": 0.62, "consumption_kwh": 1.10, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 19, "buy_price": 1.553, "sell_price": 1.353, "pv_kwh": 0.00, "consumption_kwh": 0.76, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        chg_a, _, _, _, _, _ = run_unified_dp(
            slots=slots_cheap_pv,
            current_usable=self._soc_to_usable(89.0),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.04,
            terminal_value_per_kwh=0.85,
            min_end_usable=2.0,
            config=self.config,
            remaining_hour_fraction=0.73,
        )
        chg_14 = next((s for s in chg_a if s["hour"] == 14), None)
        self.assertIsNone(chg_14, "Hour 14 must NOT charge from grid when cheap solar is coming")

        # Case B: Cheap night grid buy (0.30 PLN) with expensive daytime solar sale (0.75+ PLN).
        # Grid charge is allowed up to the volume of subsequent profitable sale.
        slots_expensive_pv = [
            {"date": "2026-09-13", "hour": 3, "buy_price": 0.30, "sell_price": 0.10, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 4, "buy_price": 0.30, "sell_price": 0.10, "pv_kwh": 0.0, "consumption_kwh": 0.4, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 12, "buy_price": 0.90, "sell_price": 0.75, "pv_kwh": 5.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 13, "buy_price": 0.90, "sell_price": 0.80, "pv_kwh": 4.0, "consumption_kwh": 0.5, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-13", "hour": 19, "buy_price": 1.50, "sell_price": 1.30, "pv_kwh": 0.0, "consumption_kwh": 0.8, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        chg_b, _, _, _, _, _ = run_unified_dp(
            slots=slots_expensive_pv,
            current_usable=self.usable_capacity * 0.20,
            usable_capacity=self.usable_capacity,
            cycle_cost=0.04,
            terminal_value_per_kwh=0.85,
            min_end_usable=2.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        chg_hours = [s["hour"] for s in chg_b]
        self.assertTrue(3 in chg_hours or 4 in chg_hours, "Cheap night charge must be allowed for subsequent profitable solar sale")

    def test_full_power_discharge_at_peak_hour(self):
        """15. Полная мощность разряда на пике цен: 19:00 разряжается на максимуме (6.6 кВт), а 20:00 забирает остаток (1.2 кВт)."""
        slots = [
            {"date": "2026-09-12", "hour": 18, "buy_price": 1.316, "sell_price": 1.117, "pv_kwh": 0.05, "consumption_kwh": 0.66, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 19, "buy_price": 1.553, "sell_price": 1.353, "pv_kwh": 0.00, "consumption_kwh": 0.66, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 20, "buy_price": 1.502, "sell_price": 1.302, "pv_kwh": 0.00, "consumption_kwh": 0.73, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 21, "buy_price": 1.307, "sell_price": 1.107, "pv_kwh": 0.00, "consumption_kwh": 0.62, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-12", "hour": 22, "buy_price": 1.195, "sell_price": 0.995, "pv_kwh": 0.00, "consumption_kwh": 0.64, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        # Start at 18:00 with near-full battery (99.6% SOC)
        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=self._soc_to_usable(99.6),
            usable_capacity=self.usable_capacity,
            cycle_cost=0.04,
            terminal_value_per_kwh=0.85,
            min_end_usable=2.0,
            config=self.config,
            remaining_hour_fraction=1.0,
        )
        dis_19 = next((s for s in dis if s["hour"] == 19), None)
        self.assertIsNotNone(dis_19, "Hour 19 must discharge")
        self.assertAlmostEqual(dis_19["planned_battery_out_kwh"], 6.6, delta=0.05,
                               msg="Hour 19 must discharge at full power 6.6 kW, not artificially capped at 6.4 kW")

    def test_two_high_peaks_morning_and_evening_arbitrage(self):
        """16. Два высоких пика за сутки: утренний (07:00) и вечерний (19:00).
        - В 03:00 зарядка от сети заблокирована (min_arbitrage_profit = 0.70).
        - В 07:00 разряд идет на максимуме инвертора (6.6 кВт).
        - В 14:00-16:00 АКБ заряжается от дешевой дневной сети под вечерний пик.
        - В 19:00 разряд идет на максимуме инвертора (6.6 кВт).
        """
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            min_energy_to_discharge=0.50,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12.0,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            battery_capacity=16.3,
            disable_discharge=False,
            min_arbitrage_profit=0.70,
            eta_charge=0.95,
            eta_discharge=0.95,
        )

        slots = [
            {"date": "2026-09-14", "hour": 0, "buy_price": 1.22, "sell_price": 1.02, "pv_kwh": 0.0, "consumption_kwh": 0.45, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 1, "buy_price": 1.18, "sell_price": 0.98, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 2, "buy_price": 1.16, "sell_price": 0.96, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 3, "buy_price": 1.14, "sell_price": 0.94, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 4, "buy_price": 1.17, "sell_price": 0.97, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 5, "buy_price": 1.24, "sell_price": 1.04, "pv_kwh": 0.0, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 6, "buy_price": 2.13, "sell_price": 1.50, "pv_kwh": 0.02, "consumption_kwh": 0.46, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 7, "buy_price": 2.47, "sell_price": 1.84, "pv_kwh": 0.12, "consumption_kwh": 0.46, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 8, "buy_price": 2.19, "sell_price": 1.56, "pv_kwh": 0.33, "consumption_kwh": 0.50, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 9, "buy_price": 1.89, "sell_price": 1.26, "pv_kwh": 0.80, "consumption_kwh": 0.56, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 10, "buy_price": 1.08, "sell_price": 0.91, "pv_kwh": 1.35, "consumption_kwh": 0.60, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 11, "buy_price": 1.01, "sell_price": 0.84, "pv_kwh": 1.70, "consumption_kwh": 0.68, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 12, "buy_price": 0.94, "sell_price": 0.77, "pv_kwh": 1.68, "consumption_kwh": 0.62, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 13, "buy_price": 0.89, "sell_price": 0.72, "pv_kwh": 1.56, "consumption_kwh": 0.34, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 14, "buy_price": 0.87, "sell_price": 0.70, "pv_kwh": 1.47, "consumption_kwh": 0.48, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 15, "buy_price": 0.91, "sell_price": 0.74, "pv_kwh": 1.19, "consumption_kwh": 0.44, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 16, "buy_price": 1.00, "sell_price": 0.83, "pv_kwh": 0.71, "consumption_kwh": 0.38, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 17, "buy_price": 1.59, "sell_price": 1.06, "pv_kwh": 0.21, "consumption_kwh": 0.56, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 18, "buy_price": 2.10, "sell_price": 1.57, "pv_kwh": 0.03, "consumption_kwh": 0.48, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 19, "buy_price": 3.15, "sell_price": 2.61, "pv_kwh": 0.00, "consumption_kwh": 0.50, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 20, "buy_price": 2.98, "sell_price": 2.44, "pv_kwh": 0.00, "consumption_kwh": 0.76, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 21, "buy_price": 2.02, "sell_price": 1.48, "pv_kwh": 0.00, "consumption_kwh": 0.64, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 22, "buy_price": 1.27, "sell_price": 1.10, "pv_kwh": 0.00, "consumption_kwh": 0.54, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 23, "buy_price": 1.14, "sell_price": 0.97, "pv_kwh": 0.00, "consumption_kwh": 0.48, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]

        # Start at midnight with 74.0% SOC
        start_soc = 74.0
        usable_capacity = 16.3 * ((100.0 - 12.0) / 100.0)
        current_usable = 16.3 * ((start_soc - 12.0) / 100.0)

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.04,
            terminal_value_per_kwh=0.85,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        chg_hours = [s["hour"] for s in chg]

        # 1. No charging during night hours 00:00-05:00
        self.assertFalse(any(h in chg_hours for h in range(6)),
                         f"Night hours 00:00-05:00 must NOT charge from grid, got chg_hours: {chg_hours}")

        # 2. Morning peak discharge at 07:00 on full power
        dis_7 = next((s for s in dis if s["hour"] == 7), None)
        self.assertIsNotNone(dis_7, "Morning peak hour 07:00 must discharge")
        self.assertAlmostEqual(dis_7["planned_battery_out_kwh"], 6.6, delta=0.05,
                               msg="Morning peak 07:00 must discharge at full power 6.6 kW")

        # 3. Daytime cheap grid charge (14:00-16:00)
        self.assertTrue(14 in chg_hours or 15 in chg_hours,
                        "Daytime cheap hours (14:00/15:00) must charge from grid for evening peak")

        # 4. Evening peak discharge at 19:00 on full power
        dis_19 = next((s for s in dis if s["hour"] == 19), None)
        self.assertIsNotNone(dis_19, "Evening peak hour 19:00 must discharge")
        self.assertAlmostEqual(dis_19["planned_battery_out_kwh"], 6.6, delta=0.05,
                               msg="Evening peak 19:00 must discharge at full power 6.6 kW")

    def test_grid_charge_min_power_and_no_peak_reserve_charge(self):
        """17. Защита от микрозаряда CV (<500 Вт) и блокировка закупки ночного резерва на пике."""
        # 16-hour scenario covering daytime cheap charge, evening peak, and night transition
        # At 17:00 buy=1.874 (pre-peak rise), battery is near 98.6% where CV limits power < 500W.
        # At 21:00 buy=2.371 (peak price), min_end_usable=3.5.
        slots = [
            {"date": "2026-09-14", "hour": 8, "buy_price": 2.187, "sell_price": 1.557, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 9, "buy_price": 1.892, "sell_price": 1.262, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 10, "buy_price": 1.275, "sell_price": 1.075, "pv_kwh": 0.3, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 11, "buy_price": 1.184, "sell_price": 0.984, "pv_kwh": 0.5, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 12, "buy_price": 1.108, "sell_price": 0.908, "pv_kwh": 0.5, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 13, "buy_price": 1.046, "sell_price": 0.846, "pv_kwh": 0.5, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 14, "buy_price": 1.020, "sell_price": 0.821, "pv_kwh": 0.5, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 15, "buy_price": 1.065, "sell_price": 0.865, "pv_kwh": 0.3, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 16, "buy_price": 1.174, "sell_price": 0.974, "pv_kwh": 0.1, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 17, "buy_price": 1.874, "sell_price": 1.243, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 18, "buy_price": 2.476, "sell_price": 1.845, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 19, "buy_price": 3.706, "sell_price": 3.075, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 20, "buy_price": 3.502, "sell_price": 2.871, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 21, "buy_price": 2.371, "sell_price": 1.741, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 22, "buy_price": 1.491, "sell_price": 1.292, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
            {"date": "2026-09-14", "hour": 23, "buy_price": 1.335, "sell_price": 1.136, "pv_kwh": 0.0, "consumption_kwh": 0.3, "ev_kwh": 0.0, "planned_boiler_kwh": 0.0},
        ]
        config = DPConfig(
            min_sell_price=0.0,
            min_discharge_price=1.20,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.5,
            battery_min_soc=12,
            battery_capacity=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=0.70,
            eta_charge=0.95,
            eta_discharge=0.95,
        )
        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=2.88,  # 36.0% initial SOC
            usable_capacity=10.56,
            cycle_cost=0.076,
            terminal_value_per_kwh=0.85,
            min_end_usable=3.5,
            config=config,
            remaining_hour_fraction=1.0,
        )
        chg_hours = [c["hour"] for c in chg]

        # 1. Micro-charge at 17:00 (<0.5 kWh, power < 500W at rising tariff) must NOT be scheduled
        self.assertNotIn(17, chg_hours, "17:00 micro-charge in CV taper phase must be inhibited")

        # 2. Peak-price grid charge at 21:00 (2.371 PLN) to fulfill min_end_usable must NOT occur
        self.assertNotIn(21, chg_hours, "21:00 grid charge at peak price 2.371 PLN must be inhibited")

        # 3. Final reserve min_end_usable (>= 3.5 kWh usable) is satisfied by end of night
        self.assertGreaterEqual(stats.get("end_usable_kwh", 0.0), 3.40,
                                "Final usable energy at end of horizon must satisfy min_end_usable=3.5 kWh")

    def test_manifest_version_matches_const(self):
        """6. Синхронизация версий: const.py VERSION обязан совпадать с manifest.json."""
        import json
        manifest_path = os.path.join(project_root, "custom_components", "ems", "manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            manifest_ver = json.load(f)["version"]
        from custom_components.ems.const import VERSION
        self.assertEqual(
            VERSION,
            manifest_ver,
            f"const.py VERSION ({VERSION}) does not match manifest.json ({manifest_ver})"
        )


    def test_fractional_slot_discharge_not_downgraded(self):
        """End-of-hour fractional slot (remaining_hour_fraction < 1.0) must not be downgraded to self_consume if rate satisfies min_grid_exp."""
        fraction = 5.0 / 60.0  # 5 minutes left in the hour
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=17,
            battery_capacity=15.0,
            min_energy_to_discharge=0.50,
        )
        slots = [
            {"date": "2026-09-14", "hour": 19, "buy_price": 3.7, "sell_price": 3.0, "pv_kwh": 0.0, "consumption_kwh": 0.04},
            {"date": "2026-09-14", "hour": 20, "buy_price": 3.5, "sell_price": 2.8, "pv_kwh": 0.0, "consumption_kwh": 0.76},
        ]
        usable_capacity = 15.0 * (1 - 0.17)
        current_usable = 15.0 * (0.53 - 0.17)

        chg_h, dis_h, pvc_h, sc_h, pim_h, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.08,
            terminal_value_per_kwh=1.2,
            min_end_usable=0.0,
            config=config,
            remaining_hour_fraction=fraction,
        )

        dis_hours = [h["hour"] for h in dis_h if h["date"] == "2026-09-14"]
        sc_hours = [h["hour"] for h in sc_h if h["date"] == "2026-09-14"]

        self.assertIn(19, dis_hours, f"Hour 19 was expected in discharge_hours, but dis_h={dis_h}, sc_h={sc_h}")
        self.assertNotIn(19, sc_hours, f"Hour 19 must NOT be downgraded to self_consume")

    def test_higher_future_sell_preempts_early_low_sell_export(self):
        """Early hour with lower sell price (18h @ 1.74 PLN) must not discharge to grid when higher sell prices exist (19h @ 2.76, 20h @ 1.87)."""
        slots = [
            {"date": "2026-09-15", "hour": 18, "buy_price": 2.37, "sell_price": 1.74, "pv_kwh": 0.04, "consumption_kwh": 0.50},
            {"date": "2026-09-15", "hour": 19, "buy_price": 3.39, "sell_price": 2.76, "pv_kwh": 0.0, "consumption_kwh": 0.76},
            {"date": "2026-09-15", "hour": 20, "buy_price": 2.50, "sell_price": 1.87, "pv_kwh": 0.0, "consumption_kwh": 0.52},
            {"date": "2026-09-15", "hour": 21, "buy_price": 1.85, "sell_price": 1.22, "pv_kwh": 0.0, "consumption_kwh": 0.52},
            {"date": "2026-09-15", "hour": 22, "buy_price": 1.30, "sell_price": 1.10, "pv_kwh": 0.0, "consumption_kwh": 0.56},
            {"date": "2026-09-15", "hour": 23, "buy_price": 1.17, "sell_price": 0.97, "pv_kwh": 0.0, "consumption_kwh": 0.50},
            {"date": "2026-09-16", "hour": 0, "buy_price": 1.10, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 1, "buy_price": 1.06, "sell_price": 0.86, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 2, "buy_price": 1.04, "sell_price": 0.84, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 3, "buy_price": 1.03, "sell_price": 0.83, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 4, "buy_price": 1.03, "sell_price": 0.83, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 5, "buy_price": 1.06, "sell_price": 0.86, "pv_kwh": 0.0, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 6, "buy_price": 1.25, "sell_price": 1.00, "pv_kwh": 0.20, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 7, "buy_price": 1.50, "sell_price": 0.80, "pv_kwh": 1.50, "consumption_kwh": 0.50},
        ]
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            battery_capacity=15.0,
            min_energy_to_discharge=0.50,
            min_arbitrage_profit=0.70,
            eta_charge=0.9,
            eta_discharge=0.9,
        )
        usable_capacity = 15.0 * (1.0 - 0.12)
        current_usable = usable_capacity

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.08333333333333333,
            terminal_value_per_kwh=1.0,
            min_end_usable=0.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        dis_hours = [d["hour"] for d in dis if d["date"] == "2026-09-15"]
        sc_hours = [s["hour"] for s in sc if s["date"] == "2026-09-15"]

        # Invariant 1: Hour 18 (cheaper sell) must NOT discharge to grid when hours 19 and 20 are more lucrative
        self.assertNotIn(18, dis_hours, f"Hour 18 should not discharge to grid, but dis_hours={dis_hours}")
        self.assertIn(18, sc_hours, f"Hour 18 should be in self_consume to cover home load")

        # Invariant 2: Hours 19 and 20 (peak sell prices) MUST discharge to grid
        self.assertIn(19, dis_hours, f"Hour 19 (peak sell 2.76) must discharge to grid")
        self.assertIn(20, dis_hours, f"Hour 20 (sell 1.87) must discharge to grid")

        # Invariant 3: Hour 20 must sell deep down to evening min SOC (~20%), not get stuck at 42%
        dis_20 = next(d for d in dis if d["hour"] == 20 and d["date"] == "2026-09-15")
        end_soc_20 = config.battery_min_soc + (dis_20["expected_end_usable_kwh"] / config.battery_capacity * 100)
        self.assertLessEqual(end_soc_20, 22.0, f"Hour 20 must discharge down near evening min SOC, got {end_soc_20:.1f}%")
        self.assertGreater(dis_20["planned_energy_kwh"], 4.0, "Hour 20 must discharge at least 4.0 kWh")

    def test_manual_override_target_soc_respected_with_asymmetric_min_soc(self):
        """Test that manual override target SOC is exactly respected when evening min SOC != morning min SOC."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=8.0,
            battery_max_charge_power=8.0,
            battery_min_soc=12,
            battery_capacity=16.83,
            min_energy_to_discharge=0.5,
            disable_discharge=False,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_arbitrage_profit=0.7,
            eta_charge=0.93,
            eta_discharge=0.93,
        )
        base_min_soc = 12.0
        current_usable = 16.83 * (68.0 - base_min_soc) / 100.0
        usable_capacity = 16.83 * (1 - base_min_soc / 100.0)

        slots = [
            {"date": "2026-09-15", "hour": 19, "buy_price": 3.388, "sell_price": 2.757, "pv_kwh": 0.0, "consumption_kwh": 0.19, "override": None},
            {"date": "2026-09-15", "hour": 20, "buy_price": 2.501, "sell_price": 1.871, "pv_kwh": 0.0, "consumption_kwh": 0.52, "override": "discharge:25.0"},
            {"date": "2026-09-15", "hour": 21, "buy_price": 1.849, "sell_price": 1.219, "pv_kwh": 0.0, "consumption_kwh": 0.52, "override": None},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.05,
            terminal_value_per_kwh=1.2,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=15.0 / 60.0,
        )

        dis_20 = next(d for d in dis if d["hour"] == 20)
        end_soc_20 = config.battery_min_soc + (dis_20["expected_end_usable_kwh"] / config.battery_capacity * 100.0)
        # Must be close to target 25.0%, NOT 20.0%!
        self.assertAlmostEqual(end_soc_20, 25.0, delta=0.5, msg=f"Override target 25.0% must be respected, got {end_soc_20:.2f}%")

    def test_dynamic_morning_min_soc_after_last_sell_hour(self):
        """After the last discharge hour, soc_limit should use morning min SOC (12%) not evening (17%).

        Scenario: hours 18-23 with discharge at 18 and 20, then idle at 21-23.
        The soc_limit for hour 20 discharge should use evening min (17%) because
        hour 20 IS the last sell hour. But self-consume at hour 21 should allow
        deeper discharge (morning limit 12%), as no more grid export remains.
        Also verifies that DPConfig.last_sell_hour_by_date is populated correctly.
        """
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=8.0,
            battery_max_charge_power=8.0,
            battery_min_soc=12,
            battery_capacity=16.83,
            min_energy_to_discharge=0.0,
            disable_discharge=False,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_arbitrage_profit=0.0,
            eta_charge=0.93,
            eta_discharge=0.93,
        )
        base_min_soc = 12.0
        current_usable = 16.83 * (55.0 - base_min_soc) / 100.0
        usable_capacity = 16.83 * (1 - base_min_soc / 100.0)

        slots = [
            {"date": "2026-09-15", "hour": 18, "buy_price": 3.00, "sell_price": 1.74, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-15", "hour": 19, "buy_price": 3.20, "sell_price": 2.50, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-15", "hour": 20, "buy_price": 2.50, "sell_price": 1.87, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-15", "hour": 21, "buy_price": 1.50, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-15", "hour": 22, "buy_price": 1.20, "sell_price": 0.60, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-15", "hour": 23, "buy_price": 1.10, "sell_price": 0.50, "pv_kwh": 0.0, "consumption_kwh": 0.3},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.05,
            terminal_value_per_kwh=0.5,
            min_end_usable=0.5,
            config=config,
            remaining_hour_fraction=0.5,
        )

        # Verify last_sell_hour_by_date was returned in stats (without mutating config in-place)
        lsh = stats.get("last_sell_hour_by_date", {})
        self.assertIn("2026-09-15", lsh,
                       "stats['last_sell_hour_by_date'] should contain discharge date")
        config.last_sell_hour_by_date = lsh

        # Check that get_min_soc returns correctly for hours after last sell
        last_sell = config.last_sell_hour_by_date["2026-09-15"]
        # After last sell hour → morning limit (12%)
        self.assertEqual(config.get_min_soc(last_sell + 1, "2026-09-15"), 12.0,
                         f"Hour after last sell ({last_sell}) should use morning min SOC 12%")
        # At last sell hour → evening limit (17%)
        self.assertEqual(config.get_min_soc(last_sell, "2026-09-15"), 17.0,
                         f"Last sell hour ({last_sell}) should use evening min SOC 17%")
        # Morning hours still use morning limit
        self.assertEqual(config.get_min_soc(5, "2026-09-15"), 12.0,
                         "Morning hours (5) should always use morning min SOC 12%")
        # No date → old behavior
        self.assertEqual(config.get_min_soc(21), 17.0,
                         "Without date parameter, evening hours should use evening min SOC 17%")

        # Check soc_limit for discharge hours: any discharge at or before last sell should have
        # soc_limit >= evening min (17%), and discharge after would have morning min (12%)
        if dis:
            for d in dis:
                if d["hour"] <= last_sell:
                    self.assertGreaterEqual(d["soc_limit"], 12.0,
                                           f"Discharge at hour {d['hour']} soc_limit should be >= base 12%")

    def test_dynamic_morning_min_soc_when_dp_runs_at_21h_after_discharge_finished(self):
        """When current hour is 21:00+, previous discharge (at 20:00) is in the past and no longer in slots.
        Remaining slots today (21, 22, 23) must still use the morning min SOC (12%) since sales are done.
        """
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=8.0,
            battery_max_charge_power=8.0,
            battery_min_soc=12,
            battery_capacity=16.83,
            min_energy_to_discharge=0.0,
            disable_discharge=False,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_arbitrage_profit=0.0,
            eta_charge=0.93,
            eta_discharge=0.93,
        )
        base_min_soc = 12.0
        current_usable = 16.83 * (18.0 - base_min_soc) / 100.0  # current SOC = 18.0%
        usable_capacity = 16.83 * (1 - base_min_soc / 100.0)

        # Slots starting at 21:00 today, then tomorrow
        slots = [
            {"date": "2026-09-15", "hour": 21, "buy_price": 1.85, "sell_price": 1.22, "pv_kwh": 0.0, "consumption_kwh": 2.0},
            {"date": "2026-09-15", "hour": 22, "buy_price": 1.30, "sell_price": 1.10, "pv_kwh": 0.0, "consumption_kwh": 0.6},
            {"date": "2026-09-15", "hour": 23, "buy_price": 1.17, "sell_price": 0.97, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-16", "hour": 0, "buy_price": 1.10, "sell_price": 0.90, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-16", "hour": 1, "buy_price": 1.06, "sell_price": 0.86, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-16", "hour": 12, "buy_price": 0.50, "sell_price": 0.30, "pv_kwh": 4.0, "consumption_kwh": 0.5},
            {"date": "2026-09-16", "hour": 19, "buy_price": 2.90, "sell_price": 2.40, "pv_kwh": 0.0, "consumption_kwh": 0.5},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.05,
            terminal_value_per_kwh=0.5,
            min_end_usable=0.5,
            config=config,
            remaining_hour_fraction=1.0,
        )
        config.last_sell_hour_by_date = stats.get("last_sell_hour_by_date", {})

        # In DPConfig, today's date has no discharge slots in [12..23], so min SOC for hours 21, 22, 23
        # must be morning min SOC (12%), allowing battery to self-consume below 17%
        self.assertEqual(config.get_min_soc(21, "2026-09-15"), 12.0)
        self.assertEqual(config.get_min_soc(22, "2026-09-15"), 12.0)
        self.assertEqual(config.get_min_soc(23, "2026-09-15"), 12.0)
        # And before 12:00 it is also 12.0%
        self.assertEqual(config.get_min_soc(8, "2026-09-15"), 12.0)

    def test_evening_sell_not_withheld_for_cheaper_morning_dump(self):
        """Evening high-price discharge (20:00 @ 1.41 PLN) must not be capped to dump in morning (07:00 @ 1.15 PLN)."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12,
            battery_capacity=16.9,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=0.50,
            eta_charge=0.93,
            eta_discharge=0.93,
            last_sell_hour_by_date={"2026-09-16": 20, "2026-09-17": 20},
        )
        base_min_soc = 12.0
        current_usable = 16.9 * (99.0 - base_min_soc) / 100.0
        usable_capacity = 16.9 * (1 - base_min_soc / 100.0)

        slots = [
            {"date": "2026-09-16", "hour": 18, "buy_price": 1.92, "sell_price": 1.29, "pv_kwh": 0.04, "consumption_kwh": 0.40},
            {"date": "2026-09-16", "hour": 19, "buy_price": 2.20, "sell_price": 1.57, "pv_kwh": 0.00, "consumption_kwh": 0.51},
            {"date": "2026-09-16", "hour": 20, "buy_price": 2.04, "sell_price": 1.41, "pv_kwh": 0.00, "consumption_kwh": 0.53},
            {"date": "2026-09-16", "hour": 21, "buy_price": 1.80, "sell_price": 1.17, "pv_kwh": 0.00, "consumption_kwh": 0.55},
            {"date": "2026-09-16", "hour": 22, "buy_price": 1.23, "sell_price": 1.03, "pv_kwh": 0.00, "consumption_kwh": 0.21},
            {"date": "2026-09-16", "hour": 23, "buy_price": 1.10, "sell_price": 0.90, "pv_kwh": 0.00, "consumption_kwh": 0.49},
            {"date": "2026-09-17", "hour": 0, "buy_price": 1.12, "sell_price": 0.92, "pv_kwh": 0.00, "consumption_kwh": 0.60},
            {"date": "2026-09-17", "hour": 1, "buy_price": 1.10, "sell_price": 0.90, "pv_kwh": 0.00, "consumption_kwh": 0.41},
            {"date": "2026-09-17", "hour": 2, "buy_price": 1.05, "sell_price": 0.85, "pv_kwh": 0.00, "consumption_kwh": 0.43},
            {"date": "2026-09-17", "hour": 3, "buy_price": 1.03, "sell_price": 0.83, "pv_kwh": 0.00, "consumption_kwh": 0.45},
            {"date": "2026-09-17", "hour": 4, "buy_price": 1.02, "sell_price": 0.82, "pv_kwh": 0.00, "consumption_kwh": 0.43},
            {"date": "2026-09-17", "hour": 5, "buy_price": 1.09, "sell_price": 0.89, "pv_kwh": 0.00, "consumption_kwh": 0.40},
            {"date": "2026-09-17", "hour": 6, "buy_price": 1.71, "sell_price": 1.07, "pv_kwh": 0.02, "consumption_kwh": 0.47},
            {"date": "2026-09-17", "hour": 7, "buy_price": 1.78, "sell_price": 1.15, "pv_kwh": 0.15, "consumption_kwh": 0.68},
            {"date": "2026-09-17", "hour": 8, "buy_price": 1.69, "sell_price": 1.06, "pv_kwh": 0.51, "consumption_kwh": 0.45},
            {"date": "2026-09-17", "hour": 12, "buy_price": 0.67, "sell_price": 0.47, "pv_kwh": 1.85, "consumption_kwh": 0.48},
            {"date": "2026-09-17", "hour": 13, "buy_price": 0.65, "sell_price": 0.45, "pv_kwh": 1.88, "consumption_kwh": 0.75},
            {"date": "2026-09-17", "hour": 19, "buy_price": 2.40, "sell_price": 1.77, "pv_kwh": 0.00, "consumption_kwh": 0.56},
            {"date": "2026-09-17", "hour": 20, "buy_price": 2.15, "sell_price": 1.52, "pv_kwh": 0.00, "consumption_kwh": 0.73},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.074,
            terminal_value_per_kwh=0.628,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        dis_map = {f"{d['date']}_{d['hour']}": d for d in dis}
        # 20:00 tonight should discharge significantly (at least 4.0 kWh), not capped at 2.1 kWh
        self.assertIn("2026-09-16_20", dis_map)
        self.assertGreater(dis_map["2026-09-16_20"]["planned_battery_out_kwh"], 4.0)
        # 07:00 tomorrow morning must NOT be a grid export discharge hour (price 1.15 vs 1.41 tonight)
        self.assertNotIn("2026-09-17_7", dis_map)

    def test_pre_peak_sell_not_throttled_to_cheaper_post_peak(self):
        """18:00 @ 1.32 PLN should discharge at full capacity and not be throttled to dump in 20:00 @ 1.26 PLN."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12,
            battery_capacity=16.9,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=0.50,
            eta_charge=0.93,
            eta_discharge=0.93,
            last_sell_hour_by_date={"2026-09-18": 20, "2026-09-19": 19},
        )
        base_min_soc = 12.0
        current_usable = 16.9 * (99.0 - base_min_soc) / 100.0  # 14.7 kWh
        usable_capacity = 16.9 * (1 - base_min_soc / 100.0)

        slots = [
            {"date": "2026-09-18", "hour": 16, "buy_price": 0.89, "sell_price": 0.69, "pv_kwh": 0.2, "consumption_kwh": 0.3},
            {"date": "2026-09-18", "hour": 17, "buy_price": 1.71, "sell_price": 1.08, "pv_kwh": 0.1, "consumption_kwh": 0.4},
            {"date": "2026-09-18", "hour": 18, "buy_price": 1.95, "sell_price": 1.32, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-18", "hour": 19, "buy_price": 2.24, "sell_price": 1.61, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-18", "hour": 20, "buy_price": 1.89, "sell_price": 1.26, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-18", "hour": 21, "buy_price": 1.61, "sell_price": 0.98, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-18", "hour": 22, "buy_price": 1.12, "sell_price": 0.92, "pv_kwh": 0.0, "consumption_kwh": 0.3},
            {"date": "2026-09-18", "hour": 23, "buy_price": 1.02, "sell_price": 0.82, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 0, "buy_price": 0.96, "sell_price": 0.76, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 1, "buy_price": 0.89, "sell_price": 0.69, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 2, "buy_price": 0.85, "sell_price": 0.65, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 3, "buy_price": 0.84, "sell_price": 0.64, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 4, "buy_price": 0.84, "sell_price": 0.64, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 5, "buy_price": 0.84, "sell_price": 0.64, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 6, "buy_price": 0.90, "sell_price": 0.70, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 7, "buy_price": 0.91, "sell_price": 0.71, "pv_kwh": 0.3, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 8, "buy_price": 0.71, "sell_price": 0.51, "pv_kwh": 1.5, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 11, "buy_price": 0.19, "sell_price": 0.00, "pv_kwh": 2.5, "consumption_kwh": 0.6},
            {"date": "2026-09-19", "hour": 18, "buy_price": 1.07, "sell_price": 0.87, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 19, "buy_price": 1.20, "sell_price": 1.00, "pv_kwh": 0.0, "consumption_kwh": 0.6},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.074,
            terminal_value_per_kwh=0.5,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        dis_map = {f"{d['date']}_{d['hour']}": d for d in dis}
        # 18:00 must discharge strongly (> 5 kWh) at 1.32 PLN, not throttled to 2.2 kWh
        self.assertIn("2026-09-18_18", dis_map)
        self.assertGreater(dis_map["2026-09-18_18"]["planned_battery_out_kwh"], 5.0)
        # 19:00 must also discharge at max capacity
        self.assertIn("2026-09-18_19", dis_map)
        self.assertGreater(dis_map["2026-09-18_19"]["planned_battery_out_kwh"], 6.0)
        # 20:00 must NOT have discharge because 18:00 and 19:00 already took available capacity
        self.assertNotIn("2026-09-18_20", dis_map)

    def test_morning_pv_export_with_cheap_midday_grid_charge(self):
        """Morning solar export (08:00 @ 0.51 PLN) is chosen when midday grid (11:00 @ 0.19 PLN) is ultra-cheap."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.6,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12,
            battery_capacity=16.9,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=0.50,
            eta_charge=0.93,
            eta_discharge=0.93,
            last_sell_hour_by_date={"2026-09-19": 19},
        )
        base_min_soc = 12.0
        current_usable = 16.9 * (13.0 - base_min_soc) / 100.0  # start morning at 13% SOC
        usable_capacity = 16.9 * (1 - base_min_soc / 100.0)

        slots = [
            {"date": "2026-09-19", "hour": 7, "buy_price": 0.91, "sell_price": 0.71, "pv_kwh": 0.3, "consumption_kwh": 0.4},
            {"date": "2026-09-19", "hour": 8, "buy_price": 0.71, "sell_price": 0.51, "pv_kwh": 1.5, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 9, "buy_price": 0.33, "sell_price": 0.13, "pv_kwh": 2.0, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 10, "buy_price": 0.20, "sell_price": 0.00, "pv_kwh": 2.2, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 11, "buy_price": 0.19, "sell_price": 0.00, "pv_kwh": 2.5, "consumption_kwh": 0.6},
            {"date": "2026-09-19", "hour": 12, "buy_price": 0.19, "sell_price": 0.00, "pv_kwh": 2.5, "consumption_kwh": 0.6},
            {"date": "2026-09-19", "hour": 13, "buy_price": 0.20, "sell_price": 0.00, "pv_kwh": 2.2, "consumption_kwh": 0.6},
            {"date": "2026-09-19", "hour": 14, "buy_price": 0.20, "sell_price": 0.00, "pv_kwh": 2.0, "consumption_kwh": 0.6},
            {"date": "2026-09-19", "hour": 15, "buy_price": 0.22, "sell_price": 0.02, "pv_kwh": 1.5, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 16, "buy_price": 0.60, "sell_price": 0.40, "pv_kwh": 0.8, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 17, "buy_price": 0.91, "sell_price": 0.71, "pv_kwh": 0.2, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 18, "buy_price": 1.07, "sell_price": 0.87, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-09-19", "hour": 19, "buy_price": 1.20, "sell_price": 1.00, "pv_kwh": 0.0, "consumption_kwh": 0.6},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.074,
            terminal_value_per_kwh=0.5,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        pvc_hours = {p["hour"] for p in pvc}
        # 08:00 must NOT be in pvc (PV charge) because exporting PV (ACT_SOL) is more profitable
        self.assertNotIn(8, pvc_hours)
        # Midday must contain grid charging
        chg_hours = {c["hour"] for c in chg}
        self.assertTrue(any(h in (11, 12, 13, 14, 15) for h in chg_hours))


    def test_evening_discharge_not_blocked_by_night_home_reserve(self):
        """Evening high-price discharge must not be blocked by night home reservation."""
        capacity = 16.3
        min_soc = 12
        usable_capacity = capacity * ((100.0 - min_soc) / 100.0)
        # Battery at 90% SOC
        current_usable = capacity * ((90.0 - min_soc) / 100.0)

        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=min_soc,
            battery_capacity=capacity,
            min_energy_to_discharge=0.5,
            disable_discharge=False,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
        )

        # Evening 19:00 has 1.24 PLN sell, night 20-06 has 0.5 kWh cons each, morning 07:00 has 1.34 PLN sell
        slots = [
            {"date": "2026-10-04", "hour": 19, "buy_price": 1.45, "sell_price": 1.24, "pv_kwh": 0.0, "consumption_kwh": 0.5},
        ]
        for h in range(20, 24):
            slots.append({"date": "2026-10-04", "hour": h, "buy_price": 1.05, "sell_price": 0.85, "pv_kwh": 0.0, "consumption_kwh": 0.5})
        for h in range(0, 7):
            slots.append({"date": "2026-10-05", "hour": h, "buy_price": 0.98, "sell_price": 0.78, "pv_kwh": 0.0, "consumption_kwh": 0.5})
        slots.append({"date": "2026-10-05", "hour": 7, "buy_price": 1.55, "sell_price": 1.34, "pv_kwh": 0.0, "consumption_kwh": 0.5})

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.074,
            terminal_value_per_kwh=0.5,
            min_end_usable=0.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        dis_hours = [d["hour"] for d in dis]
        # 19:00 must be in discharge hours
        self.assertIn(19, dis_hours)



    def test_night_grid_charge_for_day_real_deficit(self):
        """27. Night grid charge covers physical day deficit when day is more expensive."""
        config = DPConfig(
            battery_capacity=16.3,
            battery_min_soc=20.0,
            battery_max_charge_power=6.6,
            battery_max_discharge_power=6.6,
            min_sell_price=0.04,
            min_arbitrage_profit=0.5,
            eta_charge=0.9327,
            eta_discharge=0.9327,
            min_discharge_price=0.6,
            min_energy_to_discharge=0.5,
            disable_discharge=False,
            battery_min_soc_evening=20.0,
            battery_min_soc_morning=20.0,
        )

        usable_capacity = 16.3 * 0.8 # 13.04 kWh
        # Start at 03:00 with 5.7 kWh usable (~47% SOC)
        current_usable = 5.7

        # Night hour 04:00 is 0.446 PLN.
        # Day 12:00 is 0.449 PLN.
        # Day 13:00 is 0.477 PLN.
        # Evening peak 18:00-20:00 is 1.05-1.07 PLN.
        # Low solar (total ~2.9 kWh), house consumption ~17 kWh.
        slots = [
            {"date": "2026-10-10", "hour": 3, "buy_price": 0.470, "sell_price": 0.270, "pv_kwh": 0.0, "consumption_kwh": 0.71},
            {"date": "2026-10-10", "hour": 4, "buy_price": 0.446, "sell_price": 0.246, "pv_kwh": 0.0, "consumption_kwh": 0.71},
            {"date": "2026-10-10", "hour": 5, "buy_price": 0.507, "sell_price": 0.307, "pv_kwh": 0.0, "consumption_kwh": 0.66},
            {"date": "2026-10-10", "hour": 6, "buy_price": 0.651, "sell_price": 0.451, "pv_kwh": 0.0, "consumption_kwh": 0.74},
            {"date": "2026-10-10", "hour": 7, "buy_price": 0.758, "sell_price": 0.558, "pv_kwh": 0.05, "consumption_kwh": 0.66},
            {"date": "2026-10-10", "hour": 8, "buy_price": 0.789, "sell_price": 0.589, "pv_kwh": 0.16, "consumption_kwh": 0.76},
            {"date": "2026-10-10", "hour": 9, "buy_price": 0.767, "sell_price": 0.567, "pv_kwh": 0.30, "consumption_kwh": 0.94},
            {"date": "2026-10-10", "hour": 10, "buy_price": 0.671, "sell_price": 0.471, "pv_kwh": 0.46, "consumption_kwh": 1.10},
            {"date": "2026-10-10", "hour": 11, "buy_price": 0.630, "sell_price": 0.430, "pv_kwh": 0.58, "consumption_kwh": 1.19},
            {"date": "2026-10-10", "hour": 12, "buy_price": 0.449, "sell_price": 0.249, "pv_kwh": 0.49, "consumption_kwh": 0.83},
            {"date": "2026-10-10", "hour": 13, "buy_price": 0.477, "sell_price": 0.277, "pv_kwh": 0.35, "consumption_kwh": 1.46},
            {"date": "2026-10-10", "hour": 14, "buy_price": 0.541, "sell_price": 0.341, "pv_kwh": 0.30, "consumption_kwh": 0.60},
            {"date": "2026-10-10", "hour": 15, "buy_price": 0.711, "sell_price": 0.511, "pv_kwh": 0.15, "consumption_kwh": 0.53},
            {"date": "2026-10-10", "hour": 16, "buy_price": 0.812, "sell_price": 0.612, "pv_kwh": 0.05, "consumption_kwh": 0.80},
            {"date": "2026-10-10", "hour": 17, "buy_price": 1.014, "sell_price": 0.814, "pv_kwh": 0.01, "consumption_kwh": 0.63},
            {"date": "2026-10-10", "hour": 18, "buy_price": 1.057, "sell_price": 0.857, "pv_kwh": 0.0, "consumption_kwh": 1.17},
            {"date": "2026-10-10", "hour": 19, "buy_price": 1.075, "sell_price": 0.875, "pv_kwh": 0.0, "consumption_kwh": 0.83},
            {"date": "2026-10-10", "hour": 20, "buy_price": 0.972, "sell_price": 0.772, "pv_kwh": 0.0, "consumption_kwh": 0.91},
            {"date": "2026-10-10", "hour": 21, "buy_price": 0.865, "sell_price": 0.665, "pv_kwh": 0.0, "consumption_kwh": 0.86},
            {"date": "2026-10-10", "hour": 22, "buy_price": 0.856, "sell_price": 0.657, "pv_kwh": 0.0, "consumption_kwh": 0.89},
            {"date": "2026-10-10", "hour": 23, "buy_price": 0.796, "sell_price": 0.597, "pv_kwh": 0.0, "consumption_kwh": 0.84},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.074,
            terminal_value_per_kwh=0.5,
            min_end_usable=0.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        chg_hours = [c["hour"] for c in chg]
        # Must charge at cheapest hour 04:00 AM
        self.assertIn(4, chg_hours, "Must schedule grid charging at cheapest night hour 04:00")
        # Must NOT charge at expensive hour 13:00
        self.assertNotIn(13, chg_hours, "Must NOT schedule grid charging at more expensive 13:00")



    def test_pv_charged_battery_exports_despite_high_min_arbitrage_profit(self):
        """High min_arbitrage_profit restricts grid-charge arbitrage, never blocking export of PV/existing energy."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=0.60,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=12,
            battery_capacity=16.3,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=2.00,
            eta_charge=0.94,
            eta_discharge=0.94,
        )
        usable_capacity = 16.3 * (1.0 - 0.12)
        current_usable = 16.3 * (50.0 - 12.0) / 100.0

        slots = [
            {"date": "2026-11-17", "hour": 11, "buy_price": 0.70, "sell_price": 0.40, "pv_kwh": 5.0, "consumption_kwh": 0.5},
            {"date": "2026-11-17", "hour": 12, "buy_price": 0.70, "sell_price": 0.40, "pv_kwh": 4.0, "consumption_kwh": 0.5},
            {"date": "2026-11-17", "hour": 17, "buy_price": 2.47, "sell_price": 1.84, "pv_kwh": 0.0, "consumption_kwh": 0.6},
            {"date": "2026-11-17", "hour": 18, "buy_price": 2.29, "sell_price": 1.66, "pv_kwh": 0.0, "consumption_kwh": 0.6},
            {"date": "2026-11-18", "hour": 2,  "buy_price": 0.89, "sell_price": 0.69, "pv_kwh": 0.0, "consumption_kwh": 0.5},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.125,
            terminal_value_per_kwh=0.70,
            min_end_usable=1.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        # Grid arbitrage purchase must be blocked (spread 1.84 - 0.70 < 2.00)
        self.assertEqual(chg, [], f"Grid charge for arbitrage must be blocked at min_arbitrage_profit=2.00, got {chg}")
        # PV charge must fill the battery and peak export at 17:00 must run
        self.assertTrue(len(pvc) > 0, "PV surplus must charge the battery")
        dis_hours = [d["hour"] for d in dis]
        self.assertIn(17, dis_hours, f"Peak sell at 17:00 (1.84 PLN) must export PV-charged energy, got dis={dis}")



    def test_map_dp_to_physical_discharge_fallback_above_min_sell_price(self):
        """Discharge falling below min_discharge_price must map to sale_pv (NORMAL) when sell_price > min_sell_price."""
        from custom_components.ems.utils import map_dp_to_physical

        # 1. sell_price >= min_discharge_price -> sale_pv_bat
        mode, _ = map_dp_to_physical("discharge", sell_price=1.85, pv_kwh=0.0, min_sell_price=0.04, min_discharge_price=1.80, cheap_ahead=False)
        self.assertEqual(mode, "sale_pv_bat")

        # 2. sell_price < min_discharge_price, but > min_sell_price -> sale_pv (NOT stop_sale!)
        mode, _ = map_dp_to_physical("discharge", sell_price=1.66, pv_kwh=0.0, min_sell_price=0.04, min_discharge_price=1.80, cheap_ahead=False)
        self.assertEqual(mode, "sale_pv")

        # 3. sell_price <= min_sell_price -> stop_sale
        mode, _ = map_dp_to_physical("discharge", sell_price=0.02, pv_kwh=0.0, min_sell_price=0.04, min_discharge_price=1.80, cheap_ahead=False)
        self.assertEqual(mode, "stop_sale")



    def test_daily_export_budget_rerun_does_not_double_scale_forecast_or_slot0(self):
        """Daily budget cap re-run must not apply 0.85 forecast discount or remaining_hour_fraction twice."""
        config = DPConfig(
            min_sell_price=0.04,
            min_discharge_price=1.80,
            battery_max_discharge_power=9.0,
            battery_max_charge_power=11.0,
            battery_min_soc=11,
            battery_capacity=29.9,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=1.30,
            eta_charge=0.9338,
            eta_discharge=0.9338,
            tomorrow_is_forecast=True,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-18": 14.0, "2026-11-19": 14.0},
        )
        usable_capacity = 29.9 * (1.0 - 0.11)
        current_usable = 29.9 * (51.0 - 11.0) / 100.0

        slots = [
            {"date": "2026-11-18", "hour": 10, "buy_price": 2.50, "sell_price": 0.96, "pv_kwh": 0.04, "consumption_kwh": 0.50},
            {"date": "2026-11-18", "hour": 23, "buy_price": 0.93, "sell_price": 0.73, "pv_kwh": 0.00, "consumption_kwh": 0.51},
            {"date": "2026-11-19", "hour": 1,  "buy_price": 0.69, "sell_price": 0.49, "pv_kwh": 0.00, "consumption_kwh": 0.43, "override": "grid_charge:100.0"},
            {"date": "2026-11-19", "hour": 2,  "buy_price": 0.72, "sell_price": 0.52, "pv_kwh": 0.00, "consumption_kwh": 0.43, "override": "grid_charge:100.0"},
            {"date": "2026-11-19", "hour": 14, "buy_price": 1.94, "sell_price": 1.74, "pv_kwh": 0.05, "consumption_kwh": 0.43},
            # Raw sell 2.45 * 0.85 = 2.0825 (>= 1.80), while double-scaled 2.45 * 0.85^2 = 1.770 (< 1.80)
            {"date": "2026-11-19", "hour": 15, "buy_price": 3.08, "sell_price": 2.45, "pv_kwh": 0.01, "consumption_kwh": 0.36},
            # Raw sell 2.36 * 0.85 = 2.006 (>= 1.80), while double-scaled 2.36 * 0.85^2 = 1.705 (< 1.80)
            {"date": "2026-11-19", "hour": 16, "buy_price": 2.99, "sell_price": 2.36, "pv_kwh": 0.00, "consumption_kwh": 0.63},
            {"date": "2026-11-19", "hour": 17, "buy_price": 2.62, "sell_price": 1.99, "pv_kwh": 0.00, "consumption_kwh": 0.57},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.1254,
            terminal_value_per_kwh=0.82,
            min_end_usable=4.9,
            config=config,
            remaining_hour_fraction=0.5,
        )

        # Tomorrow 15:00 and 16:00 must remain above min_discharge_price (1.80) and discharge up to 14.0 kWh budget
        dis_19 = [d for d in dis if d["date"] == "2026-11-19"]
        self.assertTrue(len(dis_19) >= 2, f"Expected both 15:00 and 16:00 to discharge, got {dis_19}")
        self.assertAlmostEqual(sum(d["planned_energy_kwh"] for d in dis_19), 14.0, delta=0.15)
        # First slot (10:00) consumption must only be scaled once by remaining_hour_fraction=0.5 (0.25 kWh, not 0.125 kWh)
        sc_10 = next((s for s in sc if s["date"] == "2026-11-18" and s["hour"] == 10), None)
        self.assertIsNotNone(sc_10)
        self.assertAlmostEqual(sc_10["consumption_kwh"], 0.25, delta=0.01)


    def test_monthly_export_budget_across_today_and_tomorrow(self):
        """Monthly PV export cap must apply jointly across today and tomorrow when both are in the same month."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=1.50,
            battery_max_discharge_power=6.0,
            battery_max_charge_power=6.0,
            battery_min_soc=11,
            battery_capacity=29.9,
            battery_min_soc_evening=12.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            min_arbitrage_profit=0.20,
            eta_charge=0.95,
            eta_discharge=0.95,
            tomorrow_is_forecast=False,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-18": 14.0, "2026-11-19": 14.0},
            limit_export_to_monthly_pv=True,
            remaining_monthly_export={"2026-11": 8.0},
        )
        usable_capacity = 29.9 * (1.0 - 0.11)
        current_usable = 22.0

        slots = [
            {"date": "2026-11-18", "hour": 18, "buy_price": 2.40, "sell_price": 2.20, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-11-18", "hour": 19, "buy_price": 2.80, "sell_price": 2.60, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-11-18", "hour": 23, "buy_price": 0.80, "sell_price": 0.50, "pv_kwh": 0.0, "consumption_kwh": 0.4},
            {"date": "2026-11-19", "hour": 18, "buy_price": 2.60, "sell_price": 2.40, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-11-19", "hour": 19, "buy_price": 3.00, "sell_price": 2.80, "pv_kwh": 0.0, "consumption_kwh": 0.5},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.10,
            terminal_value_per_kwh=0.70,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        total_planned_export = sum(d["planned_export_kwh"] for d in dis)
        self.assertLessEqual(total_planned_export, 8.0 + 0.05)
        self.assertGreater(total_planned_export, 5.0)
        # Highest price slot (2026-11-19 19:00 @ 2.80) and second highest (2026-11-18 19:00 @ 2.60) should win over 2.20
        dis_keys = {(d["date"], d["hour"]) for d in dis}
        self.assertIn(("2026-11-19", 19), dis_keys)
        self.assertNotIn(("2026-11-18", 18), dis_keys)

    def test_monthly_export_zero_remaining_blocks_discharge_allows_self_consume(self):
        """When monthly PV export quota is 0.0 kWh, ACT_DIS must be blocked while self_consume still covers home load."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=1.50,
            battery_max_discharge_power=6.0,
            battery_max_charge_power=6.0,
            battery_min_soc=11,
            battery_capacity=29.9,
            battery_min_soc_evening=12.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            eta_charge=0.95,
            eta_discharge=0.95,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-18": 14.0},
            limit_export_to_monthly_pv=True,
            remaining_monthly_export={"2026-11": 0.0},
        )
        usable_capacity = 29.9 * (1.0 - 0.11)
        current_usable = 15.0

        slots = [
            {"date": "2026-11-18", "hour": 18, "buy_price": 3.00, "sell_price": 2.70, "pv_kwh": 0.0, "consumption_kwh": 0.8},
            {"date": "2026-11-18", "hour": 19, "buy_price": 2.90, "sell_price": 2.60, "pv_kwh": 0.0, "consumption_kwh": 0.8},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.10,
            terminal_value_per_kwh=0.70,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        self.assertEqual(dis, [], f"Expected zero discharge slots when monthly quota=0, got {dis}")
        self.assertEqual(stats["planned_export_kwh"], 0.0)
        self.assertEqual(len(sc), 2)

    def test_monthly_export_month_boundary_nov30_to_dec01(self):
        """Crossing from Nov 30 (4 kWh remaining) to Dec 01 (0 kWh PV yet) must allow Nov 30 export and block Dec 01."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=1.50,
            battery_max_discharge_power=6.0,
            battery_max_charge_power=6.0,
            battery_min_soc=11,
            battery_capacity=29.9,
            battery_min_soc_evening=12.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            eta_charge=0.95,
            eta_discharge=0.95,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-30": 14.0, "2026-12-01": 14.0},
            limit_export_to_monthly_pv=True,
            remaining_monthly_export={"2026-11": 4.0, "2026-12": 0.0},
        )
        usable_capacity = 29.9 * (1.0 - 0.11)
        current_usable = 18.0

        slots = [
            {"date": "2026-11-30", "hour": 19, "buy_price": 2.90, "sell_price": 2.60, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-11-30", "hour": 23, "buy_price": 0.80, "sell_price": 0.50, "pv_kwh": 0.0, "consumption_kwh": 0.5},
            {"date": "2026-12-01", "hour": 18, "buy_price": 3.20, "sell_price": 2.90, "pv_kwh": 0.0, "consumption_kwh": 0.5},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.10,
            terminal_value_per_kwh=0.70,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        dis_nov = [d for d in dis if d["date"] == "2026-11-30"]
        dis_dec = [d for d in dis if d["date"] == "2026-12-01"]
        self.assertEqual(len(dis_nov), 1)
        self.assertLessEqual(dis_nov[0]["planned_export_kwh"], 4.0 + 0.05)
        self.assertEqual(dis_dec, [], f"Dec 01 export must stay 0 before PV is generated, got {dis_dec}")

    def test_monthly_export_joint_daily_and_monthly_caps_without_double_scaling(self):
        """Joint monthly+daily re-run must keep forecast discount (0.85) and slot[0] scaling exact."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=1.80,
            battery_max_discharge_power=6.6,
            battery_max_charge_power=6.6,
            battery_min_soc=11,
            battery_capacity=29.9,
            battery_min_soc_evening=12.0,
            battery_min_soc_morning=12.0,
            min_energy_to_discharge=0.5,
            eta_charge=0.9338,
            eta_discharge=0.9338,
            tomorrow_is_forecast=True,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-18": 14.0, "2026-11-19": 14.0},
            limit_export_to_monthly_pv=True,
            remaining_monthly_export={"2026-11": 5.0},
        )
        usable_capacity = 29.9 * (1.0 - 0.11)
        current_usable = 18.0

        slots = [
            {"date": "2026-11-18", "hour": 10, "buy_price": 2.50, "sell_price": 0.96, "pv_kwh": 0.04, "consumption_kwh": 0.50},
            {"date": "2026-11-19", "hour": 15, "buy_price": 3.08, "sell_price": 2.45, "pv_kwh": 0.00, "consumption_kwh": 0.40},
            {"date": "2026-11-19", "hour": 16, "buy_price": 2.99, "sell_price": 2.36, "pv_kwh": 0.00, "consumption_kwh": 0.40},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.12,
            terminal_value_per_kwh=0.80,
            min_end_usable=2.0,
            config=config,
            remaining_hour_fraction=0.5,
        )

        self.assertTrue(len(dis) >= 1)
        self.assertLessEqual(stats["planned_export_kwh"], 5.0 + 0.05)
        sc_10 = next((s for s in sc if s["date"] == "2026-11-18" and s["hour"] == 10), None)
        self.assertIsNotNone(sc_10)
        self.assertAlmostEqual(sc_10["consumption_kwh"], 0.25, delta=0.01)

    def test_compute_monthly_counter_from_stats_boundary_and_reset(self):
        """LTS monthly helper must read pre-month boundary state, handle mid-month reset via sum, and fallback cleanly."""
        from datetime import timezone
        from custom_components.ems.statistics import compute_monthly_counter_from_stats

        tz = timezone.utc
        month_start = datetime(2026, 11, 1, 0, 0, 0, tzinfo=tz)

        # Case 1: Boundary row on Oct 31 23:00 (end = Nov 01 00:00) + mid-month counter reset where `sum` still increments
        stats_rows = [
            {"start": datetime(2026, 10, 31, 22, 0, tzinfo=tz).timestamp(), "state": 1880.0, "sum": 500.0},
            {"start": datetime(2026, 10, 31, 23, 0, tzinfo=tz).timestamp(), "state": 1885.5, "sum": 505.5},
            {"start": datetime(2026, 11, 10, 12, 0, tzinfo=tz).timestamp(), "state": 1920.5, "sum": 540.5},
            # Inverter reset its lifetime state back to 10.0 while HA `sum` kept rising by +12.0 kWh
            {"start": datetime(2026, 11, 18, 10, 0, tzinfo=tz).timestamp(), "state": 10.0, "sum": 552.5},
        ]
        res = compute_monthly_counter_from_stats(stats_rows, month_start, tz)
        self.assertIsNotNone(res)
        self.assertEqual(res["baseline_source"], "lts_boundary")
        self.assertAlmostEqual(res["baseline_state"], 1885.5, delta=0.01)
        self.assertAlmostEqual(res["closed_hours_delta"], 47.0, delta=0.01)
        self.assertAlmostEqual(res["last_closed_state"], 10.0, delta=0.01)

        # Case 2: Partial month (no row prior to month_start)
        partial_rows = [
            {"start": datetime(2026, 11, 5, 10, 0, tzinfo=tz).timestamp(), "state": 1900.0, "sum": 520.0},
            {"start": datetime(2026, 11, 18, 10, 0, tzinfo=tz).timestamp(), "state": 1969.4, "sum": 589.4},
        ]
        res_partial = compute_monthly_counter_from_stats(partial_rows, month_start, tz)
        self.assertIsNotNone(res_partial)
        self.assertEqual(res_partial["baseline_source"], "lts_partial_month")
        self.assertAlmostEqual(res_partial["closed_hours_delta"], 69.4, delta=0.01)


    def test_no_expensive_evening_or_midday_grid_charge_before_cheaper_night(self):
        """Expensive midday/evening slots (13h @ 1.67, 14h @ 1.79, 21h @ 1.98) must not GRID_CHARGE when cheaper night (00h @ 1.30) follows."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=1.80,
            battery_max_discharge_power=9.0,
            battery_max_charge_power=11.0,
            battery_min_soc=12,
            battery_capacity=29.9,
            min_energy_to_discharge=0.5,
            battery_min_soc_evening=17.0,
            battery_min_soc_morning=12.0,
            min_arbitrage_profit=1.30,
            eta_charge=0.9338,
            eta_discharge=0.9338,
            tomorrow_is_forecast=True,
            max_daily_discharge_kwh=14.0,
            remaining_daily_discharge={"2026-11-24": 14.0, "2026-11-25": 14.0},
        )
        usable_capacity = 29.9 * 0.88
        current_usable = 13.46  # 57% SOC

        slots = [
            {"date": "2026-11-24", "hour": 13, "buy_price": 1.67, "sell_price": 1.47, "pv_kwh": 0.22, "consumption_kwh": 0.61},
            {"date": "2026-11-24", "hour": 14, "buy_price": 1.79, "sell_price": 1.59, "pv_kwh": 0.11, "consumption_kwh": 0.53},
            {"date": "2026-11-24", "hour": 16, "buy_price": 2.50, "sell_price": 1.87, "pv_kwh": 0.00, "consumption_kwh": 0.53},
            {"date": "2026-11-24", "hour": 19, "buy_price": 2.33, "sell_price": 1.70, "pv_kwh": 0.00, "consumption_kwh": 1.24},
            {"date": "2026-11-24", "hour": 21, "buy_price": 1.98, "sell_price": 1.35, "pv_kwh": 0.00, "consumption_kwh": 0.56},
            {"date": "2026-11-24", "hour": 22, "buy_price": 1.41, "sell_price": 1.21, "pv_kwh": 0.00, "consumption_kwh": 0.54},
            {"date": "2026-11-24", "hour": 23, "buy_price": 1.30, "sell_price": 1.10, "pv_kwh": 0.00, "consumption_kwh": 0.50},
            {"date": "2026-11-25", "hour": 0,  "buy_price": 1.30, "sell_price": 1.10, "pv_kwh": 0.00, "consumption_kwh": 0.50},
            {"date": "2026-11-25", "hour": 8,  "buy_price": 3.33, "sell_price": 2.70, "pv_kwh": 0.31, "consumption_kwh": 0.46},
            {"date": "2026-11-25", "hour": 15, "buy_price": 3.85, "sell_price": 3.22, "pv_kwh": 0.01, "consumption_kwh": 0.56},
            {"date": "2026-11-25", "hour": 19, "buy_price": 3.43, "sell_price": 2.80, "pv_kwh": 0.00, "consumption_kwh": 1.07},
        ]

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=current_usable,
            usable_capacity=usable_capacity,
            cycle_cost=0.1254,
            terminal_value_per_kwh=1.4259,
            min_end_usable=3.81,
            config=config,
            remaining_hour_fraction=1.0,
        )

        chg_24 = [c for c in chg if c["date"] == "2026-11-24"]
        bad_hours = [c["hour"] for c in chg_24 if c["hour"] in (13, 14, 21)]
        self.assertEqual(bad_hours, [], f"Must NOT grid-charge at expensive 13h/14h/21h before cheaper night: got {chg_24}")




class _MockPriceState:
    """Minimal HA State stub for price parsing tests."""

    def __init__(self, entity_id: str, state: str, attributes: dict):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes


class TestFixedTariffSupport(unittest.TestCase):
    """Tests for independent fixed/sensor buy & sell electricity tariff modes."""

    def setUp(self):
        from homeassistant.util import dt as dt_util

        self._orig_parse_datetime = getattr(dt_util, "parse_datetime", None)
        self._orig_as_local = getattr(dt_util, "as_local", None)
        if self._orig_parse_datetime is None:
            dt_util.parse_datetime = datetime.fromisoformat
        if self._orig_as_local is None:
            dt_util.as_local = lambda d: d

    def tearDown(self):
        from homeassistant.util import dt as dt_util

        if self._orig_parse_datetime is None and hasattr(dt_util, "parse_datetime"):
            delattr(dt_util, "parse_datetime")
        if self._orig_as_local is None and hasattr(dt_util, "as_local"):
            delattr(dt_util, "as_local")

    def _make_pstryk_state(self, entity_id: str, today_price: float | None, tomorrow_price: float | None):
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        today_date = dt_util.now().date()
        tomorrow_date = today_date + timedelta(days=1)
        attrs = {}
        if today_price is not None:
            attrs["price_today"] = [
                {"start": f"{today_date}T{h:02d}:00:00+00:00", "price": today_price}
                for h in range(24)
            ]
        if tomorrow_price is not None:
            attrs["price_tomorrow"] = [
                {"start": f"{tomorrow_date}T{h:02d}:00:00+00:00", "price": tomorrow_price}
                for h in range(24)
            ]
        return _MockPriceState(entity_id, str(today_price or 0.0), attrs)

    def _make_pradcast_state(self, entity_id: str, target_date, price_kwh: float):
        attrs = {
            "date": str(target_date),
            "prices": [{"hour": h, "price_kwh": price_kwh} for h in range(24)],
        }
        return _MockPriceState(entity_id, str(price_kwh), attrs)

    def test_both_fixed_with_zero_sell_ignores_pradcast_and_forecast_discount(self):
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)
        prad_td = self._make_pradcast_state("sensor.prad_today", today, 0.90)
        prad_tmr = self._make_pradcast_state("sensor.prad_tomorrow", tomorrow, 1.10)

        res = resolve_hourly_tariffs(
            buy_mode=PRICE_MODE_FIXED,
            buy_fixed=1.15,
            buy_state=None,
            sell_mode=PRICE_MODE_FIXED,
            sell_fixed=0.0,
            sell_state=None,
            prad_today_state=prad_td,
            prad_tomorrow_state=prad_tmr,
            distribution_fee_fallback=0.40,
        )

        self.assertEqual(res["buy_today"], [1.15] * 24)
        self.assertEqual(res["buy_tomorrow"], [1.15] * 24)
        self.assertEqual(res["sell_today"], [0.0] * 24)
        self.assertEqual(res["sell_tomorrow"], [0.0] * 24)
        self.assertTrue(res["price_tomorrow_has_data"])
        self.assertEqual(res["today_source"], "fixed")
        self.assertEqual(res["tomorrow_source"], "fixed")
        self.assertFalse(res["tomorrow_is_forecast"])

    def test_hybrid_fixed_buy_and_sensor_sell_with_pradcast_forecast(self):
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)
        sell_state = self._make_pstryk_state("sensor.pstryk_sell", today_price=0.80, tomorrow_price=None)
        prad_tmr = self._make_pradcast_state("sensor.prad_tomorrow", tomorrow, 1.00)

        res = resolve_hourly_tariffs(
            buy_mode=PRICE_MODE_FIXED,
            buy_fixed=1.20,
            buy_state=None,
            sell_mode=PRICE_MODE_SENSOR,
            sell_fixed=None,
            sell_state=sell_state,
            prad_today_state=None,
            prad_tomorrow_state=prad_tmr,
            distribution_fee_fallback=0.40,
        )

        # Fixed buy must remain untouched by PradCast tomorrow forecast
        self.assertEqual(res["buy_today"], [1.20] * 24)
        self.assertEqual(res["buy_tomorrow"], [1.20] * 24)
        # Sensor sell gets Pstryk today and PradCast * 1.23 tomorrow
        self.assertEqual(res["sell_today"], [0.80] * 24)
        self.assertEqual(res["sell_tomorrow"], [1.23] * 24)
        self.assertTrue(res["price_tomorrow_has_data"])
        self.assertEqual(res["tomorrow_source"], "pradcast_forecast")
        self.assertTrue(res["tomorrow_is_forecast"])

    def test_hybrid_sensor_buy_and_fixed_zero_sell_guards_missing_tomorrow(self):
        from datetime import timedelta
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)

        # Case A: Buy sensor has today only (no tomorrow) -> must NOT mark tomorrow ready
        buy_today_only = self._make_pstryk_state("sensor.pstryk_buy", today_price=1.50, tomorrow_price=None)
        res_no_tmr = resolve_hourly_tariffs(
            buy_mode=PRICE_MODE_SENSOR,
            buy_fixed=None,
            buy_state=buy_today_only,
            sell_mode=PRICE_MODE_FIXED,
            sell_fixed=0.0,
            sell_state=None,
        )
        self.assertFalse(res_no_tmr["price_tomorrow_has_data"])
        self.assertEqual(res_no_tmr["sell_today"], [0.0] * 24)
        self.assertEqual(res_no_tmr["sell_tomorrow"], [0.0] * 24)

        # Case B: Buy sensor uses PradCast tomorrow while sell is fixed -> tomorrow ready, NO sell forecast discount
        prad_tmr = self._make_pradcast_state("sensor.prad_tomorrow", tomorrow, 1.00)
        res_with_prad = resolve_hourly_tariffs(
            buy_mode=PRICE_MODE_SENSOR,
            buy_fixed=None,
            buy_state=buy_today_only,
            sell_mode=PRICE_MODE_FIXED,
            sell_fixed=0.0,
            sell_state=None,
            prad_tomorrow_state=prad_tmr,
            distribution_fee_fallback=0.40,
        )
        self.assertTrue(res_with_prad["price_tomorrow_has_data"])
        self.assertEqual(res_with_prad["buy_tomorrow"], [1.63] * 24)
        self.assertEqual(res_with_prad["sell_tomorrow"], [0.0] * 24)
        self.assertFalse(res_with_prad["tomorrow_is_forecast"])

    def test_dp_fixed_buy_and_zero_sell_avoids_grid_charge_and_discharge(self):
        """With constant buy=1.20 and sell=0.00 across 48h, DP must neither grid-charge nor export."""
        config = DPConfig(
            min_sell_price=0.20,
            min_discharge_price=0.50,
            battery_max_discharge_power=5.0,
            battery_max_charge_power=5.0,
            battery_min_soc=15,
            battery_capacity=20.0,
            min_energy_to_discharge=0.5,
            battery_min_soc_evening=15.0,
            battery_min_soc_morning=15.0,
            min_arbitrage_profit=0.30,
            eta_charge=0.93,
            eta_discharge=0.93,
            tomorrow_is_forecast=False,
        )
        slots = []
        for day in ("2026-11-25", "2026-11-26"):
            for h in range(24):
                pv = 2.0 if 10 <= h <= 14 else 0.0
                slots.append({
                    "date": day,
                    "hour": h,
                    "buy_price": 1.20,
                    "sell_price": 0.0,
                    "pv_kwh": pv,
                    "consumption_kwh": 0.5,
                })

        chg, dis, pvc, sc, pim, stats = run_unified_dp(
            slots=slots,
            current_usable=8.0,
            usable_capacity=17.0,
            cycle_cost=0.10,
            terminal_value_per_kwh=1.20,
            min_end_usable=3.0,
            config=config,
            remaining_hour_fraction=1.0,
        )

        self.assertEqual(chg, [], f"Should never grid-charge on flat tariff, got {chg}")
        self.assertEqual(dis, [], f"Should never paid-discharge when sell=0.0, got {dis}")


if __name__ == "__main__":
    unittest.main()


