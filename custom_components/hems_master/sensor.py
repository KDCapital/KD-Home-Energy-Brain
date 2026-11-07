"""Sensor entities for HEMS Master."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STATUS_FALLBACK, STATUS_MODBUS_LOST
from .coordinator import HEMSDataUpdateCoordinator
from .entity import HEMSBaseEntity

ValueFn = Callable[[dict[str, Any]], Any]
AttrsFn = Callable[[dict[str, Any]], dict[str, Any]]


def _path(data: Mapping[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _number(data: Mapping[str, Any], *keys: str) -> float | None:
    value = _path(data, *keys)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _price_attrs(data: dict[str, Any]) -> dict[str, Any]:
    prices = _path(data, "price", "prices") or []
    return {
        "prices": prices,
        "price_unit": "EUR/kWh",
        "forecast_count": len(prices),
        "prices_today": [
            item
            for item in prices
            if isinstance(item, Mapping)
            and str(item.get("start", ""))[:10] == dt_util.now().date().isoformat()
        ],
    }


def _status_attrs(data: dict[str, Any]) -> dict[str, Any]:
    plan = _path(data, "plan") or {}
    modbus = _path(data, "modbus") or {}
    ev = _path(data, "ev") or {}
    return {
        "plan_reason": plan.get("reason"),
        "plan_note": plan.get("note"),
        "plan_applied": plan.get("applied"),
        "grid_charge": plan.get("grid_charge"),
        "target_soc": plan.get("target_soc"),
        "solar_sell": plan.get("solar_sell"),
        "modbus_connected": modbus.get("connected"),
        "modbus_write_enabled": modbus.get("write_enabled"),
        "fallback": modbus.get("fallback"),
        "ev_desired": ev.get("desired"),
        "ev_paused": ev.get("paused"),
        "ev_actuation": ev.get("actuation"),
    }


def _max_current_attrs(data: dict[str, Any]) -> dict[str, Any]:
    current = _path(data, "current") or {}
    return {
        "available_a": current.get("available"),
        "other_load_a": current.get("other_load"),
        "measured": current.get("measured"),
        "paused": current.get("ev_paused"),
        "reserve_a": current.get("reserve"),
    }


def _modbus_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return dict(_path(data, "modbus") or {})


def _last_session_attrs(data: dict[str, Any]) -> dict[str, Any]:
    session = _path(data, "financial", "last_session")
    return dict(session) if isinstance(session, Mapping) else {}


def _plan_attrs(data: dict[str, Any]) -> dict[str, Any]:
    plan = dict(_path(data, "plan") or {})
    return plan


def _price_icon(data: dict[str, Any]) -> str:
    price = _number(data, "price", "now")
    if price is None:
        return "mdi:currency-eur"
    if price <= 0:
        return "mdi:cash-plus"
    if price < 0.15:
        return "mdi:cash-check"
    if price > 0.30:
        return "mdi:cash-alert"
    return "mdi:currency-eur"


@dataclass(frozen=True, slots=True)
class HEMSSensorSpec:
    key: str
    name: str
    path: tuple[str, ...] | None = None
    value_fn: ValueFn | None = None
    attrs_fn: AttrsFn | None = None
    icon: str | Callable[[dict[str, Any]], str] | None = "mdi:chart-line"
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    precision: int | None = None
    entity_category: EntityCategory | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


SENSORS: tuple[HEMSSensorSpec, ...] = (
    # --- Financial KPI's -------------------------------------------------
    HEMSSensorSpec(
        "net_profit_today",
        "Net Profit Today",
        ("financial", "daily", "net_profit"),
        icon="mdi:cash-multiple",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ev_employer_reimbursement_today",
        "EV Employer Reimbursement Today",
        ("financial", "daily", "employer_revenue"),
        icon="mdi:briefcase-account",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ere_earnings_today",
        "ERE Earnings Today",
        ("financial", "daily", "ere_revenue"),
        icon="mdi:leaf-circle",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "battery_arbitrage_profit_today",
        "Battery Arbitrage Profit Today",
        ("financial", "daily", "arbitrage_profit"),
        icon="mdi:battery-sync",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ev_net_profit_today",
        "EV Net Profit Today",
        ("financial", "daily", "ev_net_profit"),
        icon="mdi:car-electric",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "net_profit_month",
        "Net Profit Month",
        ("financial", "monthly", "net_profit"),
        icon="mdi:calendar-month",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ev_employer_reimbursement_month",
        "EV Employer Reimbursement Month",
        ("financial", "monthly", "employer_revenue"),
        icon="mdi:briefcase-clock",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ere_earnings_month",
        "ERE Earnings Month",
        ("financial", "monthly", "ere_revenue"),
        icon="mdi:leaf",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "battery_arbitrage_profit_month",
        "Battery Arbitrage Profit Month",
        ("financial", "monthly", "arbitrage_profit"),
        icon="mdi:battery-heart-variant",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    # --- Energy ledger ---------------------------------------------------
    HEMSSensorSpec(
        "ev_energy_today",
        "EV Energy Today",
        ("financial", "daily", "ev_energy_kwh"),
        icon="mdi:ev-station",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=3,
    ),
    HEMSSensorSpec(
        "ev_energy_month",
        "EV Energy Month",
        ("financial", "monthly", "ev_energy_kwh"),
        icon="mdi:calendar-clock",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "ev_import_cost_today",
        "EV Import Cost Today",
        ("financial", "daily", "ev_import_cost"),
        icon="mdi:transmission-tower-import",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "grid_import_today",
        "Grid Import Today",
        ("financial", "daily", "grid_import_kwh"),
        icon="mdi:transmission-tower-import",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "grid_export_today",
        "Grid Export Today",
        ("financial", "daily", "grid_export_kwh"),
        icon="mdi:transmission-tower-export",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "pv_energy_today",
        "PV Energy Today",
        ("financial", "daily", "pv_energy_kwh"),
        icon="mdi:solar-power-variant",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "arbitrage_charge_today",
        "Arbitrage Charge Energy Today",
        ("financial", "daily", "arbitrage_charge_kwh"),
        icon="mdi:battery-arrow-up",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "arbitrage_discharge_today",
        "Arbitrage Discharge Energy Today",
        ("financial", "daily", "arbitrage_discharge_kwh"),
        icon="mdi:battery-arrow-down",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    # --- Current EV session ---------------------------------------------
    HEMSSensorSpec(
        "session_energy",
        "Current EV Session Energy",
        ("financial", "session", "energy_kwh"),
        icon="mdi:car-electric",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=3,
    ),
    HEMSSensorSpec(
        "session_cost",
        "Current EV Session Cost",
        ("financial", "session", "import_cost"),
        icon="mdi:cash-minus",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "session_profit",
        "Current EV Session Profit",
        ("financial", "session", "net_profit"),
        icon="mdi:cash-plus",
        unit=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
    ),
    HEMSSensorSpec(
        "last_session_energy",
        "Last EV Session Energy",
        ("financial", "last_session", "energy_kwh"),
        attrs_fn=_last_session_attrs,
        icon="mdi:history",
        unit="kWh",
        device_class=SensorDeviceClass.ENERGY,
        precision=3,
    ),
    # --- Live power proxies ---------------------------------------------
    HEMSSensorSpec(
        "pv_power",
        "PV Power",
        ("power", "pv"),
        icon="mdi:solar-power",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "grid_power",
        "Grid Power",
        ("power", "grid"),
        icon="mdi:transmission-tower",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "battery_power",
        "Battery Power",
        ("power", "battery"),
        icon="mdi:battery-charging",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "house_load",
        "House Load",
        ("power", "house"),
        icon="mdi:home-lightning-bolt",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "ev_power",
        "EV Power",
        ("power", "ev"),
        icon="mdi:car-electric",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "heatpump_power",
        "Heat Pump Power",
        ("power", "heatpump"),
        icon="mdi:heat-pump",
        unit="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "battery_soc",
        "Battery SOC",
        ("battery", "soc"),
        icon="mdi:battery-high",
        unit="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "heatpump_cop",
        "Heat Pump COP",
        ("heatpump", "cop"),
        icon="mdi:heat-pump-outline",
        state_class=SensorStateClass.MEASUREMENT,
        precision=2,
    ),
    # --- Price and balancing --------------------------------------------
    HEMSSensorSpec(
        "current_price",
        "Current Price",
        ("price", "now"),
        attrs_fn=_price_attrs,
        icon=_price_icon,
        unit="EUR/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        precision=4,
    ),
    HEMSSensorSpec(
        "max_allowed_ev_current",
        "Max Allowed EV Current",
        ("current", "max_ev"),
        attrs_fn=_max_current_attrs,
        icon="mdi:current-ac",
        unit="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
    ),
    HEMSSensorSpec(
        "available_grid_current",
        "Available Grid Current",
        ("current", "available"),
        attrs_fn=_max_current_attrs,
        icon="mdi:current-ac",
        unit="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        precision=1,
    ),
    # --- Status / diagnostics --------------------------------------------
    HEMSSensorSpec(
        "system_status",
        "System Status",
        ("status",),
        attrs_fn=_status_attrs,
        icon="mdi:state-machine",
    ),
    HEMSSensorSpec(
        "plan_reason",
        "Plan Reason",
        ("plan", "reason"),
        attrs_fn=_plan_attrs,
        icon="mdi:brain",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HEMSSensorSpec(
        "planned_target_soc",
        "Planned Target SOC",
        ("plan", "target_soc"),
        icon="mdi:battery-sync",
        unit="%",
        state_class=SensorStateClass.MEASUREMENT,
        precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HEMSSensorSpec(
        "ev_actuation",
        "EV Actuation",
        ("ev", "actuation"),
        icon="mdi:ev-station",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HEMSSensorSpec(
        "modbus_last_error",
        "Modbus Last Error",
        ("modbus", "last_error"),
        attrs_fn=_modbus_attrs,
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HEMSSensorSpec(
        "last_update",
        "Last Update",
        value_fn=lambda data: _parse_last_update(data),
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _parse_last_update(data: dict[str, Any]) -> datetime | None:
    value = _path(data, "last_update")
    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


class HEMSSensor(HEMSBaseEntity, SensorEntity):
    """One normalized HEMS sensor from the coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HEMSDataUpdateCoordinator,
        spec: HEMSSensorSpec,
    ) -> None:
        super().__init__(coordinator, f"sensor_{spec.key}")
        self.spec = spec
        self._attr_name = spec.name
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_category = spec.entity_category
        self._attr_extra_state_attributes = dict(spec.extra)

    @property
    def icon(self) -> str | None:
        icon = self.spec.icon
        if callable(icon):
            return icon(self.snapshot)
        return icon

    @property
    def native_value(self) -> Any:
        data = self.snapshot
        if self.spec.value_fn is not None:
            value = self.spec.value_fn(data)
        elif self.spec.path is not None:
            value = _path(data, *self.spec.path)
        else:
            return None
        if self.spec.device_class == SensorDeviceClass.MONETARY and value is not None:
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                return None
        if self.spec.key == "current_price" and value is not None:
            try:
                return round(float(value), 4)
            except (TypeError, ValueError):
                return None
        if self.spec.key in {"system_status", "plan_reason", "ev_actuation"}:
            return value
        if isinstance(value, float):
            return round(value, self.spec.precision if self.spec.precision is not None else 2)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.spec.attrs_fn is None:
            return dict(self.spec.extra)
        try:
            return self.spec.attrs_fn(self.snapshot)
        except Exception:  # pragma: no cover - attributes must never break state
            return {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HEMSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HEMSSensor(coordinator, spec) for spec in SENSORS)
