"""Financial and control mathematics for HEMS Master.

This module is deliberately free of Home Assistant imports. That makes the
formulas independently testable and keeps the coordinator focused on data
plumbing and actuator safety.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Sequence

# Keep this in sync with const.DEFAULT_ROUND_TRIP_EFFICIENCY without importing
# the HA-facing const module in pure tests.
DEFAULT_ROUND_TRIP_EFFICIENCY = 0.90


def _finite(value: float | int | None, default: float = 0.0) -> float:
    """Return a finite float, or *default* for None/NaN/Infinity."""
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class SessionEconomics:
    """The financial result of an amount of EV charging energy."""

    energy_kwh: float
    import_cost: float
    fixed_reimbursement: float
    dynamic_reimbursement: float
    ere_revenue: float

    @property
    def employer_revenue(self) -> float:
        return self.fixed_reimbursement + self.dynamic_reimbursement

    @property
    def total_revenue(self) -> float:
        return self.employer_revenue + self.ere_revenue

    @property
    def net_profit(self) -> float:
        return self.total_revenue - self.import_cost

    @property
    def effective_revenue_per_kwh(self) -> float:
        if self.energy_kwh <= 0:
            return 0.0
        return self.total_revenue / self.energy_kwh

    def as_dict(self) -> dict[str, float]:
        return {
            "energy_kwh": round(self.energy_kwh, 4),
            "import_cost": round(self.import_cost, 4),
            "fixed_reimbursement": round(self.fixed_reimbursement, 4),
            "dynamic_reimbursement": round(self.dynamic_reimbursement, 4),
            "employer_revenue": round(self.employer_revenue, 4),
            "ere_revenue": round(self.ere_revenue, 4),
            "total_revenue": round(self.total_revenue, 4),
            "net_profit": round(self.net_profit, 4),
        }


def calculate_session(
    energy_kwh: float,
    import_cost: float,
    employer_rate: float,
    ere_rate: float,
    dynamic_reimbursement: float = 0.0,
) -> SessionEconomics:
    """Calculate the financial balance for one EV charging session.

    ``import_cost`` is based on the bare/Eflux price. When the employer also
    reimburses that bare energy component, pass it as
    ``dynamic_reimbursement``; the fixed employer rate is added on top.

    The acceptance example from the specification (without a separate dynamic
    component) remains: 40 kWh at EUR 0.10 import, EUR 0.25 employer and
    EUR 0.124 ERE yields 10.00 + 4.96 - 4.00 = EUR 10.96 net profit.
    """
    energy = max(0.0, _finite(energy_kwh))
    cost = _finite(import_cost)
    employer = max(0.0, _finite(employer_rate))
    ere = max(0.0, _finite(ere_rate))
    dynamic = _finite(dynamic_reimbursement)
    return SessionEconomics(
        energy_kwh=energy,
        import_cost=cost,
        fixed_reimbursement=energy * employer,
        dynamic_reimbursement=dynamic,
        ere_revenue=energy * ere,
    )


def calculate_period_profit(
    employer_revenue: float,
    ere_revenue: float,
    ev_import_cost: float,
    arbitrage_discharge_value: float,
    arbitrage_charge_cost: float,
) -> float:
    """Net result for a day/month, excluding taxes and depreciation."""
    return (
        _finite(employer_revenue)
        + _finite(ere_revenue)
        - _finite(ev_import_cost)
        + _finite(arbitrage_discharge_value)
        - _finite(arbitrage_charge_cost)
    )


def ev_charging_is_profitable(
    price_per_kwh: float | None,
    employer_rate: float,
    ere_rate: float,
    minimum_margin: float,
    *,
    dynamic_reimbursed: bool = False,
) -> bool:
    """Return True when the EV charging result beats the minimum margin.

    If the employer reimburses the bare dynamic energy price, that component
    cancels against the bare import cost. The remaining margin is the fixed
    employer rate plus ERE; the bare price therefore no longer determines the
    net result. Without dynamic reimbursement the bare price is compared with
    the combined fixed/ERE income.
    """
    revenue = max(0.0, _finite(employer_rate)) + max(0.0, _finite(ere_rate))
    margin = max(0.0, _finite(minimum_margin))
    if dynamic_reimbursed:
        return revenue >= margin
    if price_per_kwh is None:
        return False
    price = _finite(price_per_kwh, math.nan)
    if not math.isfinite(price):
        return False
    return price + margin <= revenue


@dataclass(frozen=True, slots=True)
class LoadBalanceResult:
    """Outcome of one dynamic load-balancing calculation."""

    available_a: float
    target_a: int
    paused: bool
    other_load_a: float
    measured: bool
    reason: str


def calculate_ev_current(
    *,
    max_fuse_a: float,
    min_current_a: float = 6.0,
    max_current_a: float = 16.0,
    phases: int = 3,
    voltage: float = 230.0,
    grid_current_a: float | None = None,
    grid_power_w: float | None = None,
    ev_current_a: float | None = None,
    ev_power_w: float | None = None,
    heatpump_current_a: float | None = None,
    heatpump_power_w: float | None = None,
    reserve_a: float = 1.0,
    desired: bool = True,
) -> LoadBalanceResult:
    """Calculate the maximum safe EV charging current.

    ``grid_current_a``/``grid_power_w`` must describe the *whole* service,
    including the EV charger and heat pump. If they do, the EV's own current
    is subtracted to obtain the other household load. Otherwise the heat-pump
    value is used only as a fallback, never added twice.

    A 1 A reserve is held back by default. Below the EV minimum current the
    charger is paused instead of running at an unsafe or unsupported current.
    """
    fuse = max(0.0, _finite(max_fuse_a, 25.0))
    minimum = max(0.0, _finite(min_current_a, 6.0))
    maximum = max(minimum, _finite(max_current_a, 16.0))
    phases = max(1, int(_finite(phases, 3.0)))
    voltage = max(1.0, _finite(voltage, 230.0))
    reserve = max(0.0, _finite(reserve_a, 1.0))

    measured = False
    other_load = 0.0
    reason = "no_grid_measurement"

    if grid_current_a is not None:
        grid_a = _finite(grid_current_a)
        ev_a = (
            _finite(ev_current_a)
            if ev_current_a is not None
            else max(0.0, _finite(ev_power_w)) / (phases * voltage)
        )
        if grid_a >= 0:
            other_load = max(0.0, grid_a - max(0.0, ev_a))
            measured = True
            reason = "grid_current"
        else:
            # Exporting while the EV charges should not reduce the available
            # current to a negative number.
            other_load = 0.0
            measured = True
            reason = "grid_export"
    elif grid_power_w is not None:
        grid_w = _finite(grid_power_w)
        ev_a = (
            _finite(ev_current_a)
            if ev_current_a is not None
            else max(0.0, _finite(ev_power_w)) / (phases * voltage)
        )
        if grid_w >= 0:
            grid_a = grid_w / (phases * voltage)
            other_load = max(0.0, grid_a - max(0.0, ev_a))
            measured = True
            reason = "grid_power"
        else:
            other_load = 0.0
            measured = True
            reason = "grid_export"

    if not measured and heatpump_current_a is not None:
        # We cannot see the rest of the house, so this is only a coarse
        # fallback. It is intentionally marked as unmeasured.
        other_load = max(0.0, _finite(heatpump_current_a))
        reason = "heatpump_only"
    elif not measured and heatpump_power_w is not None:
        other_load = max(0.0, _finite(heatpump_power_w)) / (phases * voltage)
        reason = "heatpump_only"

    available = fuse - other_load - reserve

    if not desired:
        return LoadBalanceResult(
            available_a=round(available, 2),
            target_a=0,
            paused=True,
            other_load_a=round(other_load, 2),
            measured=measured,
            reason="not_requested",
        )

    if not measured:
        # Without a whole-service measurement we do not know the real house
        # load and therefore cannot safely permit EV charging.
        return LoadBalanceResult(
            available_a=round(available, 2),
            target_a=0,
            paused=True,
            other_load_a=round(other_load, 2),
            measured=False,
            reason=reason,
        )

    if available < minimum:
        return LoadBalanceResult(
            available_a=round(available, 2),
            target_a=0,
            paused=True,
            other_load_a=round(other_load, 2),
            measured=True,
            reason="insufficient_capacity",
        )

    target = int(math.floor(_clamp(available, 0.0, maximum)))
    if target < minimum:
        target = 0
    return LoadBalanceResult(
        available_a=round(available, 2),
        target_a=target,
        paused=target == 0,
        other_load_a=round(other_load, 2),
        measured=True,
        reason="ok" if target else "insufficient_capacity",
    )


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """One forecast price interval."""

    start: datetime
    price: float
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class ArbitrageDecision:
    """Desired inverter state produced by the price/arbitrage planner."""

    grid_charge: bool
    target_soc: int
    solar_sell: bool
    reason: str
    price: float
    peak_price: float | None = None
    spread: float | None = None
    required_spread: float | None = None


def decide_arbitrage(
    *,
    now: datetime,
    price: float | None,
    forecast: Sequence[PriceSlot],
    battery_soc: float | None,
    minimum_margin: float,
    round_trip_efficiency: float = DEFAULT_ROUND_TRIP_EFFICIENCY,
    auto_enabled: bool = True,
    force_grid_charge: bool = False,
    horizon_hours: int = 18,
) -> ArbitrageDecision | None:
    """Choose a grid-charge/discharge state for the current hour.

    The rule buys energy when the expected peak later in the horizon exceeds
    the current price by at least ``margin / efficiency``. The efficiency term
    accounts for round-trip losses. At a high price the target SOC is lowered
    so the battery can cover the expensive hours; in the middle of the day a
    70-80% target leaves room for PV while avoiding forced import.
    """
    if force_grid_charge:
        return ArbitrageDecision(
            grid_charge=True,
            target_soc=100,
            solar_sell=False,
            reason="forced",
            price=_finite(price),
        )

    if not auto_enabled or price is None or battery_soc is None:
        return None

    current_price = _finite(price, math.nan)
    soc = _clamp(_finite(battery_soc), 0.0, 100.0)
    if not math.isfinite(current_price):
        return None

    efficiency = _clamp(_finite(round_trip_efficiency, 0.90), 0.50, 1.0)
    required_spread = max(0.0, _finite(minimum_margin)) / efficiency

    horizon_end = now + timedelta(hours=max(1, int(horizon_hours)))
    future = [
        slot
        for slot in forecast
        if slot.start > now and slot.start <= horizon_end and math.isfinite(slot.price)
    ]
    peak = max((slot.price for slot in future), default=None)

    if soc >= 98.0:
        return ArbitrageDecision(
            grid_charge=False,
            target_soc=98,
            solar_sell=True,
            reason="battery_full",
            price=current_price,
            peak_price=peak,
            spread=(peak - current_price) if peak is not None else None,
            required_spread=required_spread,
        )

    if current_price <= 0:
        return ArbitrageDecision(
            grid_charge=True,
            target_soc=100,
            solar_sell=False,
            reason="negative_or_zero_price",
            price=current_price,
            peak_price=peak,
            spread=(peak - current_price) if peak is not None else None,
            required_spread=required_spread,
        )

    if peak is not None and peak - current_price >= required_spread:
        return ArbitrageDecision(
            grid_charge=True,
            target_soc=100,
            solar_sell=False,
            reason="profitable_price_spread",
            price=current_price,
            peak_price=peak,
            spread=peak - current_price,
            required_spread=required_spread,
        )

    prices = [slot.price for slot in future] or [current_price]
    typical = median(prices)
    if peak is not None and (current_price >= peak * 0.85 or current_price >= typical * 1.20):
        return ArbitrageDecision(
            grid_charge=False,
            target_soc=20,
            solar_sell=True,
            reason="high_price_discharge",
            price=current_price,
            peak_price=peak,
            spread=peak - current_price,
            required_spread=required_spread,
        )

    return ArbitrageDecision(
        grid_charge=False,
        target_soc=75,
        solar_sell=True,
        reason="self_consumption",
        price=current_price,
        peak_price=peak,
        spread=(peak - current_price) if peak is not None else None,
        required_spread=required_spread,
    )


def tou_slot_for_hour(hour: int, slots: Sequence[dict[str, object]]) -> dict[str, object] | None:
    """Return the configured TOU slot containing *hour* (0-23)."""
    for slot in slots:
        start = str(slot.get("start", "00:00"))
        end = str(slot.get("end", "00:00"))
        try:
            start_hour = int(start.split(":", 1)[0])
            end_hour = int(end.split(":", 1)[0])
        except (ValueError, IndexError):
            continue
        if start_hour <= end_hour:
            if start_hour <= hour < end_hour:
                return slot
        elif hour >= start_hour or hour < end_hour:
            return slot
    return None
