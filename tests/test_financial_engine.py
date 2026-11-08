"""Pure unit tests for the HEMS financial engine.

Run from the repository root with: ``pytest -q``.
The module is loaded by path so the tests do not need a Home Assistant runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hems_master"
    / "financial_engine.py"
)
SPEC = importlib.util.spec_from_file_location("hems_financial_engine", MODULE_PATH)
assert SPEC and SPEC.loader
financial = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = financial
SPEC.loader.exec_module(financial)


def test_acceptance_profit_example() -> None:
    result = financial.calculate_session(40, 4.00, 0.25, 0.124)
    assert round(result.employer_revenue, 2) == 10.00
    assert round(result.ere_revenue, 2) == 4.96
    assert round(result.import_cost, 2) == 4.00
    assert round(result.net_profit, 2) == 10.96


def test_negative_import_price_increases_profit() -> None:
    result = financial.calculate_session(10, -1.50, 0.25, 0.124)
    assert result.import_cost == -1.50
    assert round(result.net_profit, 2) == 5.24


def test_dynamic_employer_reimbursement_uses_full_cost() -> None:
    # 10 kWh: Eflux reimburses 10 x EUR 0.10 bare, fixed EUR 0.25,
    # ERE EUR 0.124; actual/full price is EUR 0.35.
    result = financial.calculate_session(
        10,
        3.50,
        0.25,
        0.124,
        dynamic_reimbursement=1.00,
    )
    assert round(result.employer_revenue, 2) == 3.50
    assert round(result.net_profit, 2) == 1.24
    assert financial.ev_charging_is_profitable(
        0.35,
        0.25,
        0.124,
        0.08,
        dynamic_reimbursed=True,
        bare_price_per_kwh=0.10,
    )
    assert not financial.ev_charging_is_profitable(
        0.40,
        0.25,
        0.124,
        0.08,
        dynamic_reimbursed=True,
        bare_price_per_kwh=0.10,
    )


def test_ev_charging_profitability() -> None:
    assert financial.ev_charging_is_profitable(0.20, 0.25, 0.124, 0.08)
    assert not financial.ev_charging_is_profitable(0.32, 0.25, 0.124, 0.08)
    assert financial.ev_charging_is_profitable(-0.10, 0.0, 0.124, 0.08)


def test_load_balancer_derates_ev_immediately() -> None:
    result = financial.calculate_ev_current(
        max_fuse_a=25,
        min_current_a=6,
        max_current_a=16,
        phases=3,
        voltage=230,
        grid_current_a=34,
        ev_current_a=16,
        reserve_a=1,
    )
    assert result.measured is True
    assert result.other_load_a == 18
    assert result.available_a == 6
    assert result.target_a == 6
    assert result.paused is False


def test_load_balancer_pauses_below_minimum() -> None:
    result = financial.calculate_ev_current(
        max_fuse_a=25,
        min_current_a=6,
        max_current_a=16,
        grid_current_a=30,
        ev_current_a=10,
        reserve_a=1,
    )
    assert result.available_a == 4
    assert result.target_a == 0
    assert result.paused is True


def test_load_balancer_never_exceeds_charger_max() -> None:
    result = financial.calculate_ev_current(
        max_fuse_a=63,
        min_current_a=6,
        max_current_a=16,
        grid_current_a=5,
        ev_current_a=0,
    )
    assert result.target_a == 16


def test_load_balancer_without_grid_measurement_pauses() -> None:
    result = financial.calculate_ev_current(
        max_fuse_a=25,
        min_current_a=6,
        max_current_a=16,
    )
    assert result.measured is False
    assert result.target_a == 0
    assert result.paused is True


def test_arbitrage_buys_low_for_a_later_peak() -> None:
    now = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    forecast = [
        financial.PriceSlot(now + timedelta(hours=1), 0.10),
        financial.PriceSlot(now + timedelta(hours=12), 0.35),
    ]
    decision = financial.decide_arbitrage(
        now=now,
        price=0.10,
        forecast=forecast,
        battery_soc=30,
        minimum_margin=0.08,
        round_trip_efficiency=0.90,
    )
    assert decision is not None
    assert decision.grid_charge is True
    assert decision.target_soc == 100
    assert decision.reason == "profitable_price_spread"


def test_arbitrage_charges_at_negative_price() -> None:
    now = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    decision = financial.decide_arbitrage(
        now=now,
        price=-0.03,
        forecast=[],
        battery_soc=50,
        minimum_margin=0.08,
    )
    assert decision is not None
    assert decision.grid_charge is True
    assert decision.reason == "negative_or_zero_price"


def test_arbitrage_discharges_at_high_price() -> None:
    now = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
    forecast = [
        financial.PriceSlot(now + timedelta(hours=1), 0.41),
        financial.PriceSlot(now + timedelta(hours=2), 0.38),
    ]
    decision = financial.decide_arbitrage(
        now=now,
        price=0.40,
        forecast=forecast,
        battery_soc=80,
        minimum_margin=0.08,
    )
    assert decision is not None
    assert decision.grid_charge is False
    assert decision.target_soc == 20


def test_full_battery_does_not_grid_charge() -> None:
    now = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    forecast = [financial.PriceSlot(now + timedelta(hours=1), 0.50)]
    decision = financial.decide_arbitrage(
        now=now,
        price=0.05,
        forecast=forecast,
        battery_soc=99,
        minimum_margin=0.08,
    )
    assert decision is not None
    assert decision.grid_charge is False
    assert decision.reason == "battery_full"
