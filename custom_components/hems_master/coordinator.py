"""DataUpdateCoordinator for HEMS Master.

Responsibilities:
* read the user-selected Home Assistant entities (30 s coordinator poll),
* integrate EV MID-meter energy and dynamic prices into a persistent ledger,
* calculate battery arbitrage profit and EV/ERE/employer revenue,
* run a 5 s load-balancing loop,
* evaluate the hourly price/arbitrage plan and safely apply it to the Deye,
* provide one normalized snapshot for all entities and the dashboard.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_INVERTED,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BARE_PRICE_ENTITY,
    CONF_EMPLOYER_RATE,
    CONF_EMPLOYER_PAYS_DYNAMIC,
    CONF_ERE_RATE,
    CONF_ERE_COMMISSION,
    CONF_ERE_CONVERSION_FACTOR,
    CONF_ERE_MARKET_PRICE_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONNECTED_ENTITY,
    CONF_EV_CONTROL_ENABLED,
    CONF_EV_CURRENT_ENTITY,
    CONF_EV_CURRENT_NUMBER_ENTITY,
    CONF_EV_ENERGY_ENTITY,
    CONF_EV_MODE_FAST,
    CONF_EV_MODE_MIN_SOLAR,
    CONF_EV_MODE_OFF,
    CONF_EV_MODE_SELECT_ENTITY,
    CONF_EV_MODE_SOLAR,
    CONF_EV_POWER_ENTITY,
    CONF_GRID_CURRENT_ENTITY,
    CONF_GRID_CURRENT_RESERVE,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HEATPUMP_COP_ENTITY,
    CONF_HEATPUMP_POWER_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_MAX_EV_CURRENT,
    CONF_MAX_GRID_CURRENT,
    CONF_MIN_ARBITRAGE_MARGIN,
    CONF_MIN_EV_CURRENT,
    CONF_MODBUS_CONNECTION_TYPE,
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT,
    CONF_MODBUS_WRITE_ENABLED,
    CONF_PHASES,
    CONF_PRICE_ENTITY,
    CONF_PRICE_ATTRIBUTE,
    CONF_PRICE_FORECAST_ENTITY,
    CONF_PRICE_ENTITY_IS_BARE,
    CONF_PRICE_SURCHARGE,
    CONF_PV_POWER_ENTITY,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_SOLARMAN_DEVICE_ID,
    CONF_VOLTAGE,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_EMPLOYER_RATE,
    DEFAULT_ERE_COMMISSION,
    DEFAULT_ERE_CONVERSION_FACTOR,
    DEFAULT_ERE_RATE,
    DEFAULT_EV_CONTROL_ENABLED,
    DEFAULT_EV_MODE_FAST,
    DEFAULT_EV_MODE_MIN_SOLAR,
    DEFAULT_EV_MODE_OFF,
    DEFAULT_EV_MODE_SOLAR,
    DEFAULT_GRID_CURRENT_RESERVE,
    DEFAULT_MAX_EV_CURRENT,
    DEFAULT_MAX_GRID_CURRENT,
    DEFAULT_MIN_ARBITRAGE_MARGIN,
    DEFAULT_MIN_EV_CURRENT,
    DEFAULT_MODES,
    DEFAULT_PHASES,
    DEFAULT_ROUND_TRIP_EFFICIENCY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
    LOAD_BALANCE_INTERVAL,
    MAX_ACCEPTABLE_METER_JUMP_KWH,
    MAX_HISTORY_DAYS,
    MAX_RESTORE_GAP_DAYS,
    MAX_STORED_SESSIONS,
    MODE_AUTO_ARBITRAGE,
    MODE_AUTO_EV_CHARGING,
    MODE_FORCE_EV_CHARGING,
    MODE_FORCE_GRID_CHARGE,
    MODBUS_CONNECTION_SOLARMAN,
    MODBUS_CONNECTION_TCP,
    DEFAULT_MODBUS_CONNECTION_TYPE,
    PLAN_INTERVAL,
    REASON_AUTO_OFF,
    REASON_FALLBACK,
    REASON_NO_DATA,
    REASON_WRITES_DISABLED,
    SESSION_GAP_SECONDS,
    STATUS_BATTERY_DISCHARGING,
    STATUS_EV_CHARGING,
    STATUS_FALLBACK,
    STATUS_FORCE_EV,
    STATUS_FORCE_GRID,
    STATUS_GRID_CHARGING,
    STATUS_IDLE,
    STATUS_MODBUS_LOST,
    STATUS_MONITORING,
    STATUS_SOLAR,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOU_SLOTS,
)
from .financial_engine import (
    LoadBalanceResult,
    PriceSlot,
    calculate_ev_current,
    calculate_period_profit,
    calculate_session,
    decide_arbitrage,
    ev_charging_is_profitable,
    tou_slot_for_hour,
)
from .modbus_client import (
    DeyeModbusClient,
    InverterSettings,
    ModbusDependencyError,
    ModbusError,
)
from .solarman_client import DeyeSolarmanClient

_LOGGER = logging.getLogger(__name__)

SETTING_LIMITS: dict[str, tuple[float, float]] = {
    CONF_EMPLOYER_RATE: (0.0, 5.0),
    CONF_ERE_RATE: (0.0, 2.0),
    CONF_MIN_ARBITRAGE_MARGIN: (0.0, 1.0),
    CONF_MAX_GRID_CURRENT: (1.0, 125.0),
}

PERIOD_KEYS = (
    "ev_energy_kwh",
    "ev_import_cost",
    "employer_revenue",
    "employer_fixed_revenue",
    "employer_dynamic_revenue",
    "ere_revenue",
    "arbitrage_charge_kwh",
    "arbitrage_charge_cost",
    "arbitrage_discharge_kwh",
    "arbitrage_discharge_value",
    "grid_import_kwh",
    "grid_export_kwh",
    "pv_energy_kwh",
    "house_energy_kwh",
    "house_import_cost",
)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {STATE_ON, "true", "1", "yes", "connected", "charging", "home"}:
        return True
    if text in {STATE_OFF, "false", "0", "no", "disconnected", "idle", "not_home"}:
        return False
    return None


def _empty_period() -> dict[str, float]:
    return {key: 0.0 for key in PERIOD_KEYS}


def _empty_session() -> dict[str, Any]:
    return {
        "started_at": None,
        "ended_at": None,
        "start_meter_kwh": None,
        "end_meter_kwh": None,
        "energy_kwh": 0.0,
        "import_cost": 0.0,
        "employer_revenue": 0.0,
        "employer_fixed_revenue": 0.0,
        "employer_dynamic_revenue": 0.0,
        "ere_revenue": 0.0,
        "estimated_cost": False,
    }


def _merge_period(raw: Any, fallback: dict[str, float]) -> dict[str, float]:
    result = dict(fallback)
    if isinstance(raw, Mapping):
        for key in PERIOD_KEYS:
            value = _as_float(raw.get(key))
            if value is not None:
                result[key] = value
    return result


class HEMSDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator, ledger and actuator controller."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.config: dict[str, Any] = {**entry.data, **entry.options}

        def _setting(key: str, default: float) -> float:
            value = _as_float(self.config.get(key))
            return default if value is None else value

        self.settings: dict[str, float] = {
            CONF_EMPLOYER_RATE: _setting(CONF_EMPLOYER_RATE, DEFAULT_EMPLOYER_RATE),
            CONF_ERE_RATE: _setting(CONF_ERE_RATE, DEFAULT_ERE_RATE),
            CONF_MIN_ARBITRAGE_MARGIN: _setting(
                CONF_MIN_ARBITRAGE_MARGIN, DEFAULT_MIN_ARBITRAGE_MARGIN
            ),
            CONF_MAX_GRID_CURRENT: _setting(
                CONF_MAX_GRID_CURRENT, DEFAULT_MAX_GRID_CURRENT
            ),
        }
        # Used to detect whether an options update should replace a runtime
        # number value after a reload. Without this, number changes would
        # forever override later changes made in the options flow.
        self._settings_source = dict(self.settings)
        self.modes: dict[str, bool] = dict(DEFAULT_MODES)

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self._daily_date: str | None = None
        self._monthly_key: str | None = None
        self._daily = _empty_period()
        self._monthly = _empty_period()
        self._history: dict[str, dict[str, float]] = {}
        self._sessions: list[dict[str, Any]] = []
        self._last_session: dict[str, Any] | None = None
        self._session = _empty_session()
        self._session_last_activity: datetime | None = None
        self._arbitrage_pool_kwh = 0.0

        self._last_ev_energy: float | None = None
        self._last_ev_energy_at: datetime | None = None
        self._last_interval_at: datetime | None = None
        self._last_price: float | None = None
        self._last_bare_price: float | None = None
        self._last_prices: list[dict[str, Any]] = []
        self._price_slots: list[PriceSlot] = []
        self._readings: dict[str, Any] = {}
        self._load_balance = LoadBalanceResult(
            available_a=0.0,
            target_a=0,
            paused=True,
            other_load_a=0.0,
            measured=False,
            reason="not_initialized",
        )
        self._ev_price_ok = False
        self._ev_solar_ok = False
        self._ev_connected: bool | None = None
        self._ev_desired = False
        self._grid_reserve = 1.0
        self._ev_actuation = "not_initialized"
        self._ev_actuation_error: str | None = None
        self._ev_error_at: datetime | None = None
        self._last_ev_command_target: float | None = None
        self._last_ev_switch_command: bool | None = None
        self._last_ev_mode_command: str | None = None

        self._plan: dict[str, Any] = {
            "reason": REASON_NO_DATA,
            "grid_charge": False,
            "target_soc": 100,
            "solar_sell": True,
            "slot_1_charge": False,
            "applied": False,
            "note": "",
        }
        self._last_applied: tuple[int, int, int, int] | None = None
        self._last_grid_charge_applied = False
        self._last_apply_at: datetime | None = None
        self._last_apply_result: dict[str, Any] | None = None
        self._apply_error: str | None = None
        self._connect_error: str | None = None
        self._no_plan_ticks = 0

        self._modbus_enabled = bool(self.config.get(CONF_MODBUS_ENABLED, False))
        self._modbus_write_enabled = bool(
            self._modbus_enabled and self.config.get(CONF_MODBUS_WRITE_ENABLED, False)
        )
        self._modbus_connected = False
        self._modbus_last_ok: datetime | None = None
        self._modbus_first_failure: datetime | None = None
        self._fallback_active = False
        self._needs_safe_restore = False
        self._modbus_client: DeyeModbusClient | DeyeSolarmanClient | None = None
        self._modbus_connection_type = str(
            self.config.get(
                CONF_MODBUS_CONNECTION_TYPE, DEFAULT_MODBUS_CONNECTION_TYPE
            )
        )
        if self._modbus_enabled:
            raw_connection_type = self.config.get(CONF_MODBUS_CONNECTION_TYPE)
            if raw_connection_type is None:
                # Backward compatibility for entries created before the
                # Solarman transport existed: a configured host means TCP.
                raw_connection_type = (
                    MODBUS_CONNECTION_TCP
                    if self.config.get(CONF_MODBUS_HOST)
                    else DEFAULT_MODBUS_CONNECTION_TYPE
                )
            connection_type = str(raw_connection_type)
            self._modbus_connection_type = connection_type
            if connection_type == MODBUS_CONNECTION_SOLARMAN:
                device_id = str(self.config.get(CONF_SOLARMAN_DEVICE_ID) or "").strip()
                if device_id:
                    self._modbus_client = DeyeSolarmanClient(hass, device_id)
            else:
                host = str(self.config.get(CONF_MODBUS_HOST) or "").strip()
                if host:
                    self._modbus_client = DeyeModbusClient(
                        host=host,
                        port=int(self.config.get(CONF_MODBUS_PORT, 502)),
                        slave_id=int(self.config.get(CONF_MODBUS_UNIT, 1)),
                    )

        self._fast_unsub = None
        self._plan_unsub = None
        self._state_unsub = None
        self._last_state_trigger: datetime | None = None
        self._shutdown = False
        self._fast_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=entry,
        )
        self.data = self._build_snapshot()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_initialize(self) -> None:
        """Restore persisted ledger and settings before the first refresh."""
        stored = await self._store.async_load()
        if isinstance(stored, Mapping):
            try:
                self._restore(stored)
            except Exception:
                _LOGGER.exception("Could not restore the HEMS ledger; starting fresh")
        now = dt_util.now()
        self._roll_periods(now)
        self._last_interval_at = now
        if self._session.get("started_at") and self._session_last_activity:
            if (now - self._session_last_activity).total_seconds() > SESSION_GAP_SECONDS:
                self._close_session(now)
        self.data = self._build_snapshot()

    def async_start(self) -> None:
        """Start the independent 5 s balance and 60 s planning loops."""
        if self._shutdown:
            return
        self._fast_unsub = async_track_time_interval(
            self.hass,
            self._async_fast_tick,
            timedelta(seconds=LOAD_BALANCE_INTERVAL),
        )
        self._plan_unsub = async_track_time_interval(
            self.hass,
            self._async_plan_tick,
            timedelta(seconds=PLAN_INTERVAL),
        )
        watched = [
            entity_id
            for key in (
                CONF_GRID_CURRENT_ENTITY,
                CONF_GRID_POWER_ENTITY,
                CONF_EV_POWER_ENTITY,
                CONF_EV_CURRENT_ENTITY,
                CONF_EV_CHARGER_SWITCH_ENTITY,
            )
            if (entity_id := self.config.get(key))
        ]
        if watched:
            self._state_unsub = async_track_state_change_event(
                self.hass, watched, self._async_source_changed
            )

    async def _async_source_changed(self, event: Event) -> None:
        """React to a grid/EV update without waiting for the 5 s timer."""
        if self._shutdown or self._fast_lock.locked():
            return
        now = dt_util.now()
        if self._last_state_trigger is not None and (
            now - self._last_state_trigger
        ).total_seconds() < 1.0:
            return
        self._last_state_trigger = now
        await self._async_fast_tick(now)

    async def async_shutdown(self) -> None:
        self._shutdown = True
        if self._fast_unsub is not None:
            self._fast_unsub()
            self._fast_unsub = None
        if self._plan_unsub is not None:
            self._plan_unsub()
            self._plan_unsub = None
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        try:
            await self._store.async_save(self._build_store())
        except Exception:  # pragma: no cover - shutdown must not fail
            _LOGGER.exception("Could not save HEMS ledger during shutdown")
        if self._modbus_client is not None:
            await self._modbus_client.async_close()
        parent_shutdown = getattr(super(), "async_shutdown", None)
        if parent_shutdown is not None:
            result = parent_shutdown()
            if inspect.isawaitable(result):
                await result

    async def _async_update_data(self) -> dict[str, Any]:
        if self._shutdown:
            return self.data or self._build_snapshot()
        now = dt_util.now()
        self._roll_periods(now)
        try:
            readings = self._read_all(now)
            self._integrate_energy(readings, now)
            self._update_load_balance(readings)
            self._readings = readings
            await self._async_update_modbus_status(now)
            await self._async_evaluate_and_apply(readings, now)
        except Exception as err:
            _LOGGER.error("Unexpected error during HEMS coordinator update: %s", err)
            raise UpdateFailed(f"HEMS update failed: {err}") from err
        self._schedule_save()
        return self._build_snapshot()

    async def _async_fast_tick(self, now: datetime) -> None:
        """Five-second load balancing without writing the storage ledger."""
        if self._shutdown or self._fast_lock.locked():
            return
        async with self._fast_lock:
            try:
                readings = self._read_all(dt_util.now())
                self._update_load_balance(readings)
                self._readings = readings
                await self._async_control_ev(readings, dt_util.now())
            except Exception:
                _LOGGER.exception("Unexpected error during HEMS load-balancing tick")
            self.async_set_updated_data(self._build_snapshot())

    async def _async_plan_tick(self, now: datetime) -> None:
        """Re-evaluate the hourly plan and refresh Modbus connectivity."""
        if self._shutdown or self._plan_lock.locked():
            return
        async with self._plan_lock:
            try:
                readings = self._read_all(dt_util.now())
                self._update_load_balance(readings)
                self._readings = readings
                await self._async_update_modbus_status(dt_util.now())
                await self._async_evaluate_and_apply(readings, dt_util.now())
            except Exception:
                _LOGGER.exception("Unexpected error during HEMS plan tick")
            self.async_set_updated_data(self._build_snapshot())

    # ------------------------------------------------------------------
    # State reading and normalization
    # ------------------------------------------------------------------
    def _get_state(self, key: str) -> State | None:
        entity_id = self.config.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state

    @staticmethod
    def _unit(state: State | None) -> str:
        if state is None:
            return ""
        return str(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or "").strip().lower()

    def _read_power(self, key: str, *, invert: bool = False) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = self._unit(state).replace(" ", "")
        if unit in {"kw"}:
            value *= 1000.0
        elif unit in {"mw"}:
            value *= 1_000_000.0
        elif unit in {"kva"}:
            value *= 1000.0
        # W, VA and unit-less values are assumed to be watts.
        return -value if invert else value

    def _read_energy(self, key: str) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = self._unit(state).replace(" ", "")
        if unit == "wh":
            value /= 1000.0
        elif unit == "mwh":
            value *= 1000.0
        # kWh and unit-less MID meter values are assumed to be kWh.
        return value

    def _read_current(self, key: str) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = self._unit(state).replace(" ", "")
        if unit == "ma":
            value /= 1000.0
        return value

    def _read_soc(self, key: str) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = self._unit(state)
        if value <= 1.0 and unit != "%" and "percent" not in unit:
            value *= 100.0
        return max(0.0, min(100.0, value))

    def _read_bool(self, key: str) -> bool | None:
        state = self._get_state(key)
        if state is None:
            return None
        return _as_bool(state.state)

    def _read_price(self, key: str = CONF_PRICE_ENTITY) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = self._unit(state).replace(" ", "")
        if "ct/kwh" in unit or "cent/kwh" in unit:
            value /= 100.0
        elif "eur/mwh" in unit:
            value /= 1000.0
        return value

    def _read_entity_attribute(self, key: str, path: str) -> float | None:
        """Read a numeric nested state attribute, e.g. full_block.total_price."""
        state = self._get_state(key)
        if state is None or not path:
            return None
        value: Any = state.attributes
        for part in path.split("."):
            if not isinstance(value, Mapping):
                return None
            value = value.get(part)
        return _as_float(value)

    def _read_all(self, now: datetime) -> dict[str, Any]:
        price_state = self._get_state(CONF_PRICE_ENTITY)
        sensor_price = self._read_price()
        price_attribute = str(self.config.get(CONF_PRICE_ATTRIBUTE) or "").strip()
        attribute_price = (
            self._read_entity_attribute(CONF_PRICE_ENTITY, price_attribute)
            if price_attribute
            else None
        )
        if attribute_price is not None:
            # Some price integrations expose the bare/dynamic component as the
            # state and the all-in price as an attribute on the same entity.
            sensor_price = attribute_price
            bare_sensor = self._read_price(CONF_PRICE_ENTITY)
        else:
            bare_sensor = self._read_price(CONF_BARE_PRICE_ENTITY)
        surcharge = _as_float(self.config.get(CONF_PRICE_SURCHARGE), 0.0) or 0.0
        price_is_bare = bool(
            self.config.get(CONF_PRICE_ENTITY_IS_BARE, False)
        )
        if price_is_bare and attribute_price is None:
            bare_price = bare_sensor if bare_sensor is not None else sensor_price
            price = (
                bare_price + surcharge
                if bare_price is not None
                else None
            )
        else:
            price = sensor_price
            bare_price = bare_sensor
            if bare_price is None and price is not None:
                bare_price = price - surcharge if surcharge else price
        forecast_state = self._get_state(CONF_PRICE_FORECAST_ENTITY) or price_state
        prices, price_slots = self._parse_price_forecast(forecast_state, now)
        if price_is_bare and surcharge and attribute_price is None:
            for entry in prices:
                entry["price"] = round(float(entry["price"]) + surcharge, 6)
            price_slots = [
                PriceSlot(start=slot.start, end=slot.end, price=slot.price + surcharge)
                for slot in price_slots
            ]
        if not prices and price is not None:
            prices = [
                {
                    "start": now.replace(minute=0, second=0, microsecond=0).isoformat(),
                    "end": None,
                    "price": round(price, 6),
                }
            ]
            price_slots = [
                PriceSlot(
                    start=now.replace(minute=0, second=0, microsecond=0),
                    price=price,
                )
            ]
        self._last_prices = prices
        self._price_slots = price_slots
        if price is not None:
            self._last_price = price
        if bare_price is not None:
            self._last_bare_price = bare_price

        ere_market_price = self._read_price(CONF_ERE_MARKET_PRICE_ENTITY)
        ere_factor_value = _as_float(
            self.config.get(CONF_ERE_CONVERSION_FACTOR),
            DEFAULT_ERE_CONVERSION_FACTOR,
        )
        ere_factor = (
            DEFAULT_ERE_CONVERSION_FACTOR
            if ere_factor_value is None
            else ere_factor_value
        )
        ere_commission = _as_float(
            self.config.get(CONF_ERE_COMMISSION), DEFAULT_ERE_COMMISSION
        )
        ere_commission = 0.0 if ere_commission is None else ere_commission
        if ere_market_price is not None:
            ere_rate = max(
                0.0,
                ere_market_price
                * max(0.0, ere_factor)
                * max(0.0, 1.0 - ere_commission / 100.0),
            )
        else:
            ere_rate = self.settings[CONF_ERE_RATE]

        phases = max(1, int(_as_float(self.config.get(CONF_PHASES), DEFAULT_PHASES) or 3))
        voltage = max(1.0, _as_float(self.config.get(CONF_VOLTAGE), DEFAULT_VOLTAGE) or 230.0)

        pv = self._read_power(CONF_PV_POWER_ENTITY)
        grid = self._read_power(
            CONF_GRID_POWER_ENTITY,
            invert=bool(self.config.get(CONF_GRID_POWER_INVERTED, False)),
        )
        battery = self._read_power(
            CONF_BATTERY_POWER_ENTITY,
            invert=bool(self.config.get(CONF_BATTERY_POWER_INVERTED, False)),
        )
        ev = self._read_power(CONF_EV_POWER_ENTITY)
        heatpump = self._read_power(CONF_HEATPUMP_POWER_ENTITY)
        house = self._read_power(CONF_HOUSE_LOAD_ENTITY)
        if house is None and pv is not None and grid is not None and battery is not None:
            house = pv + grid - battery - (ev or 0.0) - (heatpump or 0.0)
            house = max(0.0, house)

        grid_current = self._read_current(CONF_GRID_CURRENT_ENTITY)
        if grid_current is None and grid is not None:
            grid_current = grid / (phases * voltage)
        ev_current = self._read_current(CONF_EV_CURRENT_ENTITY)
        if ev_current is None and ev is not None:
            ev_current = ev / (phases * voltage)
        heatpump_current = None
        # The heat-pump power is already part of a whole-service grid current;
        # it is only used as a fallback when no grid measurement exists.
        if (
            self._get_state(CONF_GRID_CURRENT_ENTITY) is None
            and self._get_state(CONF_GRID_POWER_ENTITY) is None
            and heatpump is not None
        ):
            heatpump_current = heatpump / (phases * voltage)

        return {
            "price": price,
            "bare_price": bare_price,
            "prices": prices,
            "ere_market_price": ere_market_price,
            "ere_rate": ere_rate,
            "ere_factor": ere_factor,
            "ere_commission": ere_commission,
            "pv": pv,
            "grid": grid,
            "battery": battery,
            "battery_soc": self._read_soc(CONF_BATTERY_SOC_ENTITY),
            "ev": ev,
            "heatpump": heatpump,
            "house": house,
            "house_estimated": self._get_state(CONF_HOUSE_LOAD_ENTITY) is None
            and house is not None,
            "grid_current": grid_current,
            "ev_current": ev_current,
            "heatpump_current": heatpump_current,
            "ev_energy_total": self._read_energy(CONF_EV_ENERGY_ENTITY),
            "ev_connected": self._read_bool(CONF_EV_CONNECTED_ENTITY),
            "charger_on": self._read_bool(CONF_EV_CHARGER_SWITCH_ENTITY),
            "heatpump_cop": self._state_float(CONF_HEATPUMP_COP_ENTITY),
            "phases": phases,
            "voltage": voltage,
            "now": now,
        }

    def _state_float(self, key: str) -> float | None:
        state = self._get_state(key)
        if state is None:
            return None
        return _as_float(state.state)

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            parsed = dt_util.utc_from_timestamp(timestamp)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            # Only unambiguous millisecond epoch strings are accepted. A
            # ten-digit string could also be YYYYMMDDHH and must not become
            # a 1970 timestamp.
            if text.isdigit() and len(text) == 13:
                return HEMSDataUpdateCoordinator._parse_dt(float(text))
            if text.isdigit() and len(text) == 10:
                seconds = int(text)
                if 1_000_000_000 <= seconds <= 4_200_000_000:
                    return HEMSDataUpdateCoordinator._parse_dt(seconds)
            parsed = dt_util.parse_datetime(text)
        else:
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.now().tzinfo)
        return dt_util.as_local(parsed)

    def _parse_price_forecast(
        self, state: State | None, now: datetime
    ) -> tuple[list[dict[str, Any]], list[PriceSlot]]:
        if state is None:
            return [], []
        attributes = state.attributes
        candidates: list[Any] = []
        for first, second in (("raw_today", "raw_tomorrow"), ("today", "tomorrow")):
            combined: list[Any] = []
            for key in (first, second):
                raw = attributes.get(key)
                if isinstance(raw, (list, tuple)):
                    combined.extend(raw)
            if combined:
                candidates = combined
                break
        if not candidates:
            for key in ("prices", "forecast", "price_forecast"):
                raw = attributes.get(key)
                if isinstance(raw, (list, tuple)) and raw:
                    candidates = list(raw)
                    break
        if not candidates:
            return [], []

        entries: list[dict[str, Any]] = []
        slots: list[PriceSlot] = []
        seen: set[str] = set()
        unit = self._unit(state).replace(" ", "")
        price_factor = 1.0
        if "ct/kwh" in unit or "cent/kwh" in unit:
            price_factor = 0.01
        elif "eur/mwh" in unit:
            price_factor = 0.001
        numeric_start = dt_util.start_of_local_day(now)
        numeric_index = 0
        for item in candidates:
            start_dt: datetime | None = None
            end_dt: datetime | None = None
            price_value: Any = None
            if isinstance(item, Mapping):
                for key in (
                    "start",
                    "from",
                    "time",
                    "start_time",
                    "datetime",
                    "timestamp",
                    "hour",
                ):
                    if key in item:
                        start_dt = self._parse_dt(item.get(key))
                        if start_dt is not None:
                            break
                for key in ("end", "till", "end_time"):
                    if key in item:
                        end_dt = self._parse_dt(item.get(key))
                        break
                for key in (
                    "total_price_eur_kwh",
                    "price",
                    "value",
                    "total",
                    "energy_price",
                    "market_price",
                ):
                    if key in item:
                        candidate_price = item.get(key)
                        if isinstance(candidate_price, Mapping):
                            if "total" in candidate_price:
                                candidate_price = candidate_price["total"]
                            elif "value" in candidate_price:
                                candidate_price = candidate_price["value"]
                            else:
                                candidate_price = None
                        price_value = candidate_price
                        break
            elif isinstance(item, (int, float)):
                start_dt = numeric_start + timedelta(hours=numeric_index)
                numeric_index += 1
                price_value = item
            else:
                continue

            price_float = _as_float(price_value)
            if start_dt is None or price_float is None:
                continue
            price_float *= price_factor
            key = start_dt.isoformat()
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat() if end_dt else None,
                    "price": round(price_float, 6),
                }
            )
            slots.append(PriceSlot(start=start_dt, end=end_dt, price=price_float))
        entries.sort(key=lambda entry: entry["start"])
        slots.sort(key=lambda slot: slot.start)
        return entries, slots

    # ------------------------------------------------------------------
    # Ledger and integration
    # ------------------------------------------------------------------
    def _integrate_energy(self, readings: Mapping[str, Any], now: datetime) -> None:
        dt_hours = 0.0
        if self._last_interval_at is not None:
            seconds = (now - self._last_interval_at).total_seconds()
            if 0 < seconds <= 300:
                dt_hours = seconds / 3600.0
            elif seconds > 300:
                _LOGGER.debug("Skipping %.0f s energy integration gap", seconds)

        self._process_ev_meter(readings, now)

        price = _as_float(readings.get("price"))
        if price is None:
            price = self._last_price
        if price is not None:
            self._last_price = price
        bare_price = _as_float(readings.get("bare_price"))
        if bare_price is None:
            bare_price = self._last_bare_price
        if bare_price is not None:
            self._last_bare_price = bare_price

        if dt_hours > 0:
            self._integrate_grid_pv(readings, dt_hours)
            self._integrate_house_cost(readings, dt_hours, price)
            self._integrate_arbitrage(readings, dt_hours, price)

        self._last_interval_at = now

    def _process_ev_meter(self, readings: Mapping[str, Any], now: datetime) -> None:
        total = _as_float(readings.get("ev_energy_total"))
        if total is None:
            return
        last = self._last_ev_energy
        if last is None:
            self._last_ev_energy = total
            self._last_ev_energy_at = now
            return

        delta = total - last
        if delta < -0.001 or delta > MAX_ACCEPTABLE_METER_JUMP_KWH:
            _LOGGER.warning(
                "Ignoring impossible MID meter delta %.3f kWh (old %.3f, new %.3f)",
                delta,
                last,
                total,
            )
            if self._session.get("started_at"):
                self._close_session(now, end_meter=total)
            self._last_ev_energy = total
            self._last_ev_energy_at = now
            return

        if delta <= 0.0001:
            self._last_ev_energy = total
            self._last_ev_energy_at = now
            return

        if self._last_ev_energy_at is not None:
            gap = (now - self._last_ev_energy_at).total_seconds()
            if gap > MAX_RESTORE_GAP_DAYS * 86400:
                _LOGGER.warning(
                    "MID meter changed after %.0f days offline; rebasing without billing",
                    gap / 86400,
                )
                self._last_ev_energy = total
                self._last_ev_energy_at = now
                return

        full_price = _as_float(readings.get("price"))
        estimated = full_price is None
        if full_price is None:
            full_price = self._last_price
        if full_price is None:
            full_price = 0.0
            estimated = True

        bare_price = _as_float(readings.get("bare_price"))
        if bare_price is None:
            bare_price = self._last_bare_price
        if bare_price is None:
            bare_price = full_price

        employer_rate = self.settings[CONF_EMPLOYER_RATE]
        ere_rate = _as_float(readings.get("ere_rate"))
        if ere_rate is None:
            ere_rate = self.settings[CONF_ERE_RATE]
        employer_pays_dynamic = bool(
            self.config.get(CONF_EMPLOYER_PAYS_DYNAMIC, False)
        )
        dynamic_reimbursement = delta * bare_price if employer_pays_dynamic else 0.0
        economics = calculate_session(
            delta,
            delta * full_price,
            employer_rate,
            ere_rate,
            dynamic_reimbursement=dynamic_reimbursement,
        )
        self._ensure_session(now, start_meter=total - delta)
        session = self._session
        session["energy_kwh"] = float(session["energy_kwh"]) + delta
        session["import_cost"] = float(session["import_cost"]) + economics.import_cost
        session["employer_fixed_revenue"] = (
            float(session["employer_fixed_revenue"]) + economics.fixed_reimbursement
        )
        session["employer_dynamic_revenue"] = (
            float(session["employer_dynamic_revenue"])
            + economics.dynamic_reimbursement
        )
        session["employer_revenue"] = (
            float(session["employer_revenue"]) + economics.employer_revenue
        )
        session["ere_revenue"] = float(session["ere_revenue"]) + economics.ere_revenue
        session["estimated_cost"] = bool(session["estimated_cost"]) or estimated
        session["end_meter_kwh"] = total
        self._session_last_activity = now

        for period in (self._daily, self._monthly):
            period["ev_energy_kwh"] += delta
            period["ev_import_cost"] += economics.import_cost
            period["employer_revenue"] += economics.employer_revenue
            period["employer_fixed_revenue"] += economics.fixed_reimbursement
            period["employer_dynamic_revenue"] += economics.dynamic_reimbursement
            period["ere_revenue"] += economics.ere_revenue

        self._last_ev_energy = total
        self._last_ev_energy_at = now
        self._schedule_save()

    def _integrate_grid_pv(
        self, readings: Mapping[str, Any], dt_hours: float
    ) -> None:
        grid = _as_float(readings.get("grid"))
        pv = _as_float(readings.get("pv"))
        if grid is not None:
            if grid >= 0:
                energy = grid / 1000.0 * dt_hours
                self._daily["grid_import_kwh"] += energy
                self._monthly["grid_import_kwh"] += energy
            else:
                energy = -grid / 1000.0 * dt_hours
                self._daily["grid_export_kwh"] += energy
                self._monthly["grid_export_kwh"] += energy
        if pv is not None and pv > 0:
            energy = pv / 1000.0 * dt_hours
            self._daily["pv_energy_kwh"] += energy
            self._monthly["pv_energy_kwh"] += energy

    def _integrate_house_cost(
        self,
        readings: Mapping[str, Any],
        dt_hours: float,
        full_price: float | None,
    ) -> None:
        """Integrate non-EV grid import at the full consumer price."""
        grid = _as_float(readings.get("grid"))
        ev = _as_float(readings.get("ev")) or 0.0
        if grid is None or grid <= 0 or full_price is None:
            return
        non_ev_import_w = max(0.0, grid - max(0.0, ev))
        energy_kwh = non_ev_import_w / 1000.0 * dt_hours
        cost = energy_kwh * full_price
        self._daily["house_energy_kwh"] += energy_kwh
        self._daily["house_import_cost"] += cost
        self._monthly["house_energy_kwh"] += energy_kwh
        self._monthly["house_import_cost"] += cost

    def _integrate_arbitrage(
        self, readings: Mapping[str, Any], dt_hours: float, price: float | None
    ) -> None:
        battery = _as_float(readings.get("battery"))
        grid = _as_float(readings.get("grid"))
        ev = _as_float(readings.get("ev")) or 0.0
        heatpump = _as_float(readings.get("heatpump")) or 0.0
        house = _as_float(readings.get("house"))
        if battery is None or grid is None or price is None:
            return

        grid_import = max(0.0, grid)
        grid_export = max(0.0, -grid)
        battery_charge = max(0.0, battery)
        battery_discharge = max(0.0, -battery)
        non_ev_import = max(0.0, grid_import - max(0.0, ev))
        grid_to_battery = min(battery_charge, non_ev_import)
        charge_kwh = grid_to_battery / 1000.0 * dt_hours
        efficiency_value = _as_float(
            self.config.get(CONF_ROUND_TRIP_EFFICIENCY),
            DEFAULT_ROUND_TRIP_EFFICIENCY,
        )
        efficiency = max(
            0.5,
            min(1.0, DEFAULT_ROUND_TRIP_EFFICIENCY if efficiency_value is None else efficiency_value),
        )

        if charge_kwh > 0:
            charge_cost = charge_kwh * price
            # The pool represents usable AC-side energy after round-trip
            # losses. Charging 1 kWh therefore adds less than 1 kWh to it.
            self._arbitrage_pool_kwh += charge_kwh * efficiency
            self._daily["arbitrage_charge_kwh"] += charge_kwh
            self._daily["arbitrage_charge_cost"] += charge_cost
            self._monthly["arbitrage_charge_kwh"] += charge_kwh
            self._monthly["arbitrage_charge_cost"] += charge_cost

        if battery_discharge <= 0 or self._arbitrage_pool_kwh <= 0:
            return

        if house is not None:
            total_load = max(0.0, house) + max(0.0, ev) + max(0.0, heatpump)
            discharge_to_load = min(battery_discharge, total_load)
        elif grid_export > 0:
            discharge_to_load = max(0.0, battery_discharge - grid_export)
        else:
            discharge_to_load = battery_discharge

        eligible_kwh = min(
            self._arbitrage_pool_kwh,
            discharge_to_load / 1000.0 * dt_hours,
        )
        discharged_kwh = battery_discharge / 1000.0 * dt_hours
        # Drain the pool for all discharge, including EV charging and export.
        # Only the part that serves local load gets the avoided-import value.
        self._arbitrage_pool_kwh = max(0.0, self._arbitrage_pool_kwh - discharged_kwh)
        if eligible_kwh <= 0:
            return
        value = eligible_kwh * price
        self._daily["arbitrage_discharge_kwh"] += eligible_kwh
        self._daily["arbitrage_discharge_value"] += value
        self._monthly["arbitrage_discharge_kwh"] += eligible_kwh
        self._monthly["arbitrage_discharge_value"] += value

    def _ensure_session(self, now: datetime, *, start_meter: float | None) -> None:
        if self._session.get("started_at"):
            if (
                self._session_last_activity is not None
                and (now - self._session_last_activity).total_seconds()
                > SESSION_GAP_SECONDS
            ):
                self._close_session(now)
            else:
                return
        self._session = _empty_session()
        self._session["started_at"] = now.isoformat()
        self._session["start_meter_kwh"] = start_meter
        self._session_last_activity = now

    def _close_session(self, now: datetime, *, end_meter: float | None = None) -> None:
        if not self._session.get("started_at"):
            return
        session = dict(self._session)
        session["ended_at"] = now.isoformat()
        if end_meter is not None:
            session["end_meter_kwh"] = end_meter
        energy = float(session.get("energy_kwh") or 0.0)
        cost = float(session.get("import_cost") or 0.0)
        session["avg_import_price"] = round(cost / energy, 4) if energy else 0.0
        session["net_profit"] = round(
            float(session.get("employer_revenue") or 0.0)
            + float(session.get("ere_revenue") or 0.0)
            - cost,
            4,
        )
        self._last_session = session
        self._sessions.append(session)
        if len(self._sessions) > MAX_STORED_SESSIONS:
            self._sessions = self._sessions[-MAX_STORED_SESSIONS:]
        self._session = _empty_session()
        self._session_last_activity = None
        self._schedule_save()

    def _roll_periods(self, now: datetime) -> None:
        today = now.date().isoformat()
        month = now.strftime("%Y-%m")
        if self._daily_date is None:
            self._daily_date = today
        elif self._daily_date != today:
            self._archive_daily(self._daily_date, self._daily)
            self._daily_date = today
            self._daily = _empty_period()
        if self._monthly_key is None:
            self._monthly_key = month
        elif self._monthly_key != month:
            self._monthly_key = month
            self._monthly = _empty_period()

    def _archive_daily(self, day: str, period: Mapping[str, Any]) -> None:
        self._history[day] = self._period_with_profit(period)
        if len(self._history) > MAX_HISTORY_DAYS:
            for key in sorted(self._history)[: len(self._history) - MAX_HISTORY_DAYS]:
                del self._history[key]

    # ------------------------------------------------------------------
    # Load balancing and EV actuator
    # ------------------------------------------------------------------
    def _update_load_balance(self, readings: Mapping[str, Any]) -> None:
        phases = int(readings.get("phases") or 3)
        voltage = float(readings.get("voltage") or 230.0)
        fuse = self.settings[CONF_MAX_GRID_CURRENT]
        minimum_value = _as_float(
            self.config.get(CONF_MIN_EV_CURRENT), DEFAULT_MIN_EV_CURRENT
        )
        maximum_value = _as_float(
            self.config.get(CONF_MAX_EV_CURRENT), DEFAULT_MAX_EV_CURRENT
        )
        reserve_value = _as_float(
            self.config.get(CONF_GRID_CURRENT_RESERVE), DEFAULT_GRID_CURRENT_RESERVE
        )
        minimum = DEFAULT_MIN_EV_CURRENT if minimum_value is None else minimum_value
        maximum = DEFAULT_MAX_EV_CURRENT if maximum_value is None else maximum_value
        reserve = (
            DEFAULT_GRID_CURRENT_RESERVE if reserve_value is None else reserve_value
        )
        self._grid_reserve = reserve
        full_price = _as_float(readings.get("price"))
        bare_price = _as_float(readings.get("bare_price"))
        self._ev_price_ok = ev_charging_is_profitable(
            full_price,
            self.settings[CONF_EMPLOYER_RATE],
            _as_float(readings.get("ere_rate"), self.settings[CONF_ERE_RATE])
            or 0.0,
            self.settings[CONF_MIN_ARBITRAGE_MARGIN],
            dynamic_reimbursed=bool(
                self.config.get(CONF_EMPLOYER_PAYS_DYNAMIC, False)
            ),
            bare_price_per_kwh=bare_price,
        )
        grid = _as_float(readings.get("grid"))
        min_ev_power = minimum * phases * voltage * 0.8
        self._ev_solar_ok = grid is not None and -grid >= min_ev_power

        connected = readings.get("ev_connected")
        self._ev_connected = connected if isinstance(connected, bool) else None
        auto_control = self.modes[MODE_AUTO_EV_CHARGING]
        force = self.modes[MODE_FORCE_EV_CHARGING]
        desired = bool(force or auto_control and (self._ev_price_ok or self._ev_solar_ok))
        if self._ev_connected is False:
            desired = False
        self._ev_desired = desired
        self._load_balance = calculate_ev_current(
            max_fuse_a=fuse,
            min_current_a=minimum,
            max_current_a=maximum,
            phases=phases,
            voltage=voltage,
            grid_current_a=_as_float(readings.get("grid_current")),
            grid_power_w=grid if readings.get("grid_current") is None else None,
            ev_current_a=_as_float(readings.get("ev_current")),
            ev_power_w=_as_float(readings.get("ev")),
            heatpump_current_a=_as_float(readings.get("heatpump_current")),
            heatpump_power_w=_as_float(readings.get("heatpump")),
            reserve_a=reserve,
            desired=desired if (force or auto_control) else True,
        )

    def _entity_domain(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is not None:
            return state.domain
        return entity_id.split(".", 1)[0] if "." in entity_id else None

    def _select_options(self, entity_id: str | None) -> list[str]:
        if not entity_id:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        options = state.attributes.get("options")
        if not isinstance(options, (list, tuple)):
            return []
        return [str(option) for option in options]

    @staticmethod
    def _numeric_option(option: str) -> float | None:
        match = re.search(r"\d+(?:[.,]\d+)?", option)
        if match is None:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except ValueError:
            return None

    def _current_option_for_target(self, entity_id: str, target: float) -> str | None:
        """Pick the highest select option that does not exceed the target."""
        choices: list[tuple[float, str]] = []
        for option in self._select_options(entity_id):
            value = self._numeric_option(option)
            if value is not None and value > 0:
                choices.append((value, option))
        if not choices:
            return None
        eligible = [(value, option) for value, option in choices if value <= target]
        if not eligible:
            return min(choices, key=lambda item: item[0])[1]
        return max(eligible, key=lambda item: item[0])[1]

    def _mode_option(
        self, entity_id: str, configured: str, fallback_keywords: tuple[str, ...]
    ) -> str:
        options = self._select_options(entity_id)
        if not options:
            return configured
        for option in options:
            if option == configured:
                return option
        configured_lower = configured.strip().lower()
        for option in options:
            if option.strip().lower() == configured_lower:
                return option

        scored: list[tuple[int, int, str]] = []
        for option in options:
            lowered = option.strip().lower()
            score = sum(1 for keyword in fallback_keywords if keyword in lowered)
            if score:
                # Prefer more matched keywords; on a tie prefer the shorter,
                # less specific label (Solar over Min+Solar).
                scored.append((score, -len(option), option))
        if scored:
            return max(scored)[2]
        return configured

    async def _async_set_ev_current(self, entity_id: str, target: float) -> bool:
        domain = self._entity_domain(entity_id)
        if domain == "number":
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": target},
                blocking=True,
            )
            self._last_ev_command_target = target
            return True
        if domain == "select":
            option = self._current_option_for_target(entity_id, target)
            if option is None:
                raise ValueError(
                    f"No numeric current options found on select entity {entity_id}"
                )
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": option},
                blocking=True,
            )
            self._last_ev_command_target = target
            return True
        raise ValueError(
            f"Unsupported EV current setpoint domain '{domain}' for {entity_id}"
        )

    async def _async_set_ev_mode(self, entity_id: str, option: str) -> None:
        state = self.hass.states.get(entity_id)
        if state is not None and state.state.strip().lower() == option.strip().lower():
            self._last_ev_mode_command = option
            return
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )
        self._last_ev_mode_command = option

    async def _async_stop_ev(self) -> None:
        """Stop the EV session through the configured actuator."""
        mode_entity = self.config.get(CONF_EV_MODE_SELECT_ENTITY)
        switch_entity = self.config.get(CONF_EV_CHARGER_SWITCH_ENTITY)
        current_entity = self.config.get(CONF_EV_CURRENT_NUMBER_ENTITY)
        mode_off = str(self.config.get(CONF_EV_MODE_OFF) or DEFAULT_EV_MODE_OFF)
        try:
            if mode_entity:
                option = self._mode_option(
                    mode_entity, mode_off, ("off", "uit", "stop", "idle")
                )
                await self._async_set_ev_mode(mode_entity, option)
            elif switch_entity:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": switch_entity},
                    blocking=True,
                )
            elif current_entity and self._entity_domain(current_entity) == "number":
                await self._async_set_ev_current(current_entity, 0)
            elif current_entity:
                self._ev_actuation = "mode_entity_required_to_stop"
                self._ev_actuation_error = None
                return
            self._ev_actuation = "stopped"
            self._ev_actuation_error = None
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not stop the EV charger: %s", err)
            self._ev_actuation = "error"
            self._ev_actuation_error = str(err)

    async def _async_control_ev(
        self, readings: Mapping[str, Any], now: datetime
    ) -> None:
        if not bool(self.config.get(CONF_EV_CONTROL_ENABLED, DEFAULT_EV_CONTROL_ENABLED)):
            self._ev_actuation = "disabled"
            return
        force = self.modes[MODE_FORCE_EV_CHARGING]
        auto_control = self.modes[MODE_AUTO_EV_CHARGING]
        if not force and not auto_control:
            self._ev_actuation = "manual"
            return

        switch_entity = self.config.get(CONF_EV_CHARGER_SWITCH_ENTITY)
        current_entity = self.config.get(CONF_EV_CURRENT_NUMBER_ENTITY)
        mode_entity = self.config.get(CONF_EV_MODE_SELECT_ENTITY)
        if not switch_entity and not current_entity and not mode_entity:
            self._ev_actuation = "no_actuator"
            return
        if self._ev_error_at is not None and (now - self._ev_error_at).total_seconds() < 60:
            return
        if self._load_balance.paused:
            target = 0.0
        else:
            target = float(self._load_balance.target_a)
        if self._ev_desired is False or not self._load_balance.measured:
            target = 0.0

        charger_on = readings.get("charger_on")
        if isinstance(charger_on, bool):
            switch_is_on = charger_on
        else:
            switch_is_on = None

        mode_off = str(
            self.config.get(CONF_EV_MODE_OFF) or DEFAULT_EV_MODE_OFF
        )
        mode_solar = str(
            self.config.get(CONF_EV_MODE_SOLAR) or DEFAULT_EV_MODE_SOLAR
        )
        mode_min_solar = str(
            self.config.get(CONF_EV_MODE_MIN_SOLAR) or DEFAULT_EV_MODE_MIN_SOLAR
        )
        mode_fast = str(
            self.config.get(CONF_EV_MODE_FAST) or DEFAULT_EV_MODE_FAST
        )

        try:
            if target <= 0:
                if mode_entity:
                    off_option = self._mode_option(
                        mode_entity, mode_off, ("off", "uit", "stop", "idle")
                    )
                    await self._async_set_ev_mode(mode_entity, off_option)
                elif switch_entity and switch_is_on is not False:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": switch_entity},
                        blocking=True,
                    )
                    self._last_ev_switch_command = False
                elif current_entity and self._last_ev_command_target != 0.0:
                    if not switch_entity:
                        if self._entity_domain(current_entity) == "number":
                            await self._async_set_ev_current(current_entity, 0)
                        else:
                            self._ev_actuation = "mode_entity_required_to_stop"
                            self._ev_actuation_error = None
                            return
                self._ev_actuation = (
                    "grid_measurement_unavailable"
                    if not self._load_balance.measured
                    else "paused"
                    if self._load_balance.paused
                    else "idle_until_conditions"
                )
                self._ev_actuation_error = None
                return

            # A charger without current control cannot safely be started by
            # HEMS. It can still be stopped when needed.
            if current_entity is None:
                self._ev_actuation = "current_control_unavailable"
                return

            if self._last_ev_command_target != target:
                await self._async_set_ev_current(current_entity, target)

            if switch_entity and switch_is_on is False:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": switch_entity},
                    blocking=True,
                )
                self._last_ev_switch_command = True

            if mode_entity:
                if force:
                    desired_mode = mode_fast
                    keywords = ("fast", "max", "force", "now")
                elif self._ev_solar_ok:
                    desired_mode = mode_solar
                    keywords = ("solar", "pv", "zon")
                else:
                    desired_mode = mode_min_solar
                    keywords = ("min", "solar", "grid", "pv")
                option = self._mode_option(mode_entity, desired_mode, keywords)
                await self._async_set_ev_mode(mode_entity, option)
            elif switch_entity and switch_is_on is None:
                self._ev_actuation = "switch_state_unknown"
                return
            self._ev_actuation = "charging"
            self._ev_actuation_error = None
        except Exception as err:  # noqa: BLE001 - service calls are user-configured
            _LOGGER.warning("Could not control the EV charger: %s", err)
            self._ev_actuation = "error"
            self._ev_actuation_error = str(err)
            self._ev_error_at = now
            self._last_ev_command_target = None
            self._last_ev_switch_command = None
            self._last_ev_mode_command = None

    # ------------------------------------------------------------------
    # Modbus plan and safety
    # ------------------------------------------------------------------
    async def _async_update_modbus_status(self, now: datetime) -> None:
        if not self._modbus_enabled or self._modbus_client is None:
            self._modbus_connected = False
            return
        try:
            async with asyncio.timeout(12):
                connected = await self._modbus_client.async_ping()
        except ModbusDependencyError:
            raise
        except (TimeoutError, ModbusError, OSError) as err:
            connected = False
            self._connect_error = str(err)
        except Exception as err:  # noqa: BLE001 - keep the coordinator alive
            connected = False
            self._connect_error = str(err)

        if connected:
            was_fallback = self._fallback_active
            self._modbus_connected = True
            self._modbus_last_ok = now
            self._modbus_first_failure = None
            self._connect_error = None
            if was_fallback:
                self._fallback_active = False
                self._needs_safe_restore = True
                _LOGGER.warning(
                    "Deye Modbus communication restored; applying a safe state before resuming"
                )
            return

        self._modbus_connected = False
        if self._modbus_client is not None:
            self._connect_error = self._modbus_client.last_error or self._connect_error
        if self._modbus_first_failure is None:
            self._modbus_first_failure = now
        elapsed = (now - self._modbus_first_failure).total_seconds()
        if elapsed >= 60 and not self._fallback_active:
            self._fallback_active = True
            _LOGGER.error(
                "Deye Modbus unavailable for >=60 s; HEMS is in fallback and will not write"
            )

    def _register_comm_failure(self, now: datetime) -> None:
        self._modbus_connected = False
        if self._modbus_first_failure is None:
            self._modbus_first_failure = now
        if (now - self._modbus_first_failure).total_seconds() >= 60:
            self._fallback_active = True

    async def _async_evaluate_and_apply(
        self, readings: Mapping[str, Any], now: datetime, *, force_apply: bool = False
    ) -> None:
        force_grid = self.modes[MODE_FORCE_GRID_CHARGE]
        settings: InverterSettings | None = None
        reason = REASON_AUTO_OFF
        decision_payload: dict[str, Any] = {}

        if force_grid:
            settings = InverterSettings(
                grid_charge=True,
                target_soc=100,
                solar_sell=False,
                slot_1_charge=True,
                reason="forced",
            )
            reason = "forced"
        elif self.modes[MODE_AUTO_ARBITRAGE]:
            price = _as_float(readings.get("price"))
            soc = _as_float(readings.get("battery_soc"))
            if price is None or soc is None:
                reason = REASON_NO_DATA
            else:
                decision = decide_arbitrage(
                    now=now,
                    price=price,
                    forecast=self._price_slots,
                    battery_soc=soc,
                    minimum_margin=self.settings[CONF_MIN_ARBITRAGE_MARGIN],
                    round_trip_efficiency=float(
                        _as_float(
                            self.config.get(CONF_ROUND_TRIP_EFFICIENCY),
                            DEFAULT_ROUND_TRIP_EFFICIENCY,
                        )
                        or DEFAULT_ROUND_TRIP_EFFICIENCY
                    ),
                    auto_enabled=True,
                )
                if decision is not None:
                    settings = InverterSettings(
                        grid_charge=decision.grid_charge,
                        target_soc=decision.target_soc,
                        solar_sell=decision.solar_sell,
                        slot_1_charge=decision.grid_charge,
                        reason=decision.reason,
                    )
                    reason = decision.reason
                    decision_payload = {
                        "price": round(decision.price, 5),
                        "peak_price": (
                            round(decision.peak_price, 5)
                            if decision.peak_price is not None
                            else None
                        ),
                        "spread": (
                            round(decision.spread, 5)
                            if decision.spread is not None
                            else None
                        ),
                        "required_spread": (
                            round(decision.required_spread, 5)
                            if decision.required_spread is not None
                            else None
                        ),
                    }

        restore_needed = self._needs_safe_restore and not force_grid
        if restore_needed:
            settings = InverterSettings(
                grid_charge=False,
                target_soc=100,
                solar_sell=True,
                slot_1_charge=False,
                reason="fallback_recovery",
            )
            reason = "fallback_recovery"

        plan = {
            "reason": reason,
            "grid_charge": bool(settings.grid_charge) if settings else False,
            "target_soc": settings.target_soc if settings else None,
            "solar_sell": bool(settings.solar_sell) if settings else None,
            "slot_1_charge": bool(settings.slot_1_charge) if settings else False,
            "applied": False,
            "applied_at": None,
            "note": "",
            "decision": decision_payload,
        }

        if settings is None:
            self._no_plan_ticks += 1
            # Do not leave a previous grid-charge command active forever when
            # price or SOC data disappears. Three consecutive empty plans mean
            # at least ~90 seconds without a valid decision.
            if (
                self._no_plan_ticks >= 3
                and self._modbus_write_enabled
                and not self._fallback_active
                and self._last_grid_charge_applied
            ):
                _LOGGER.warning(
                    "No valid arbitrage data for three ticks; switching grid charging off"
                )
                await self._async_safe_off("stale_data")
                return
            plan["note"] = "No inverter write: automatic control is off or has insufficient data"
            self._plan = plan
            return
        self._no_plan_ticks = 0

        if not self._modbus_write_enabled:
            plan["note"] = "Modbus write is disabled (monitor only)"
            plan["reason"] = f"{reason}:{REASON_WRITES_DISABLED}"
            self._needs_safe_restore = False
            self._plan = plan
            return
        if self._fallback_active and not force_grid:
            plan["note"] = "Fallback active: no Modbus writes until communication recovers"
            plan["reason"] = f"{reason}:{REASON_FALLBACK}"
            self._plan = plan
            return

        applied = await self._async_apply_inverter(
            settings, now=now, force=force_apply or force_grid or restore_needed
        )
        plan["applied"] = applied
        plan["applied_at"] = now.isoformat() if applied else None
        plan["last_result"] = self._last_apply_result
        if applied:
            plan["note"] = "Settings verified and inverter re-locked"
            self._needs_safe_restore = False
        else:
            plan["note"] = self._apply_error or "Settings were not applied"
        self._plan = plan

    async def _async_apply_inverter(
        self, settings: InverterSettings, *, now: datetime, force: bool = False
    ) -> bool:
        if not self._modbus_write_enabled or self._modbus_client is None:
            return False
        if self._fallback_active:
            return False
        if settings.key() == self._last_applied and not force:
            return True
        async with self._apply_lock:
            if settings.key() == self._last_applied and not force:
                return True
            try:
                async with asyncio.timeout(30):
                    result = await self._modbus_client.async_apply_settings(settings)
            except (TimeoutError, ModbusError, OSError) as err:
                self._apply_error = str(err)
                self._last_applied = None
                self._last_apply_result = None
                self._register_comm_failure(now)
                _LOGGER.error("Deye settings write failed: %s", err)
                return False
            except Exception as err:  # noqa: BLE001 - defensive
                self._apply_error = str(err)
                self._last_applied = None
                self._last_apply_result = None
                self._register_comm_failure(now)
                _LOGGER.exception("Unexpected Deye settings write failure")
                return False
            self._last_applied = settings.key()
            self._last_grid_charge_applied = settings.grid_charge
            self._last_apply_at = now
            self._last_apply_result = {
                key: value for key, value in result.items() if key != "verified"
            }
            self._modbus_last_ok = now
            self._modbus_connected = True
            self._modbus_first_failure = None
            self._connect_error = None
            self._apply_error = None
            self._schedule_save()
            return True

    async def async_force_apply(self) -> None:
        now = dt_util.now()
        readings = self._read_all(now)
        self._update_load_balance(readings)
        self._readings = readings
        await self._async_evaluate_and_apply(readings, now, force_apply=True)
        self.async_set_updated_data(self._build_snapshot())

    async def _async_safe_off(self, reason: str) -> None:
        if not self._modbus_write_enabled:
            self._plan = {
                "reason": reason,
                "grid_charge": False,
                "target_soc": 100,
                "solar_sell": True,
                "slot_1_charge": False,
                "applied": False,
                "applied_at": None,
                "note": "Modbus write is disabled",
                "decision": {},
            }
            return
        now = dt_util.now()
        settings = InverterSettings(
            grid_charge=False,
            target_soc=100,
            solar_sell=True,
            slot_1_charge=False,
            reason=reason,
        )
        applied = await self._async_apply_inverter(settings, now=now, force=True)
        note = "Safe state applied and verified"
        if not applied:
            note = self._apply_error or "Safe state could not be applied"
        self._plan = {
            "reason": reason,
            "grid_charge": False,
            "target_soc": 100,
            "solar_sell": True,
            "slot_1_charge": False,
            "applied": applied,
            "applied_at": now.isoformat() if applied else None,
            "note": note,
            "decision": {},
        }

    # ------------------------------------------------------------------
    # Public controls and services
    # ------------------------------------------------------------------
    async def async_set_mode(self, mode: str, value: bool) -> bool:
        if mode not in self.modes:
            raise ValueError(f"Unknown HEMS mode: {mode}")
        value = bool(value)
        self.modes[mode] = value
        if mode == MODE_AUTO_ARBITRAGE and not value:
            self.modes[MODE_FORCE_GRID_CHARGE] = False
            await self._async_safe_off("auto_off")
        elif mode == MODE_AUTO_ARBITRAGE and value:
            await self.async_force_apply()
        elif mode == MODE_FORCE_GRID_CHARGE and not value:
            await self._async_safe_off("force_grid_off")
        elif mode == MODE_FORCE_GRID_CHARGE and value:
            await self.async_force_apply()
        elif mode == MODE_FORCE_EV_CHARGING and not value and not self.modes[
            MODE_AUTO_EV_CHARGING
        ]:
            # Turning off a forced session must not leave EVCC in Fast mode.
            await self._async_stop_ev()
        self._schedule_save()
        self.async_set_updated_data(self._build_snapshot())
        return value

    async def async_set_setting(self, key: str, value: float) -> float:
        if key not in SETTING_LIMITS:
            raise ValueError(f"Unknown HEMS setting: {key}")
        low, high = SETTING_LIMITS[key]
        numeric = _as_float(value)
        if numeric is None:
            raise ValueError("Setting must be a finite number")
        numeric = max(low, min(high, numeric))
        self.settings[key] = numeric
        self._schedule_save()
        if self.data is not None:
            self.async_set_updated_data(self._build_snapshot())
        return numeric

    async def async_reset_today(self) -> None:
        now = dt_util.now()
        if self._session.get("started_at"):
            self._close_session(now)
        if self._daily_date:
            self._history.pop(self._daily_date, None)
        self._daily = _empty_period()
        self._arbitrage_pool_kwh = 0.0
        self._daily_date = now.date().isoformat()
        await self._store.async_save(self._build_store())
        self.async_set_updated_data(self._build_snapshot())

    def export_audit(self) -> dict[str, Any]:
        """Return an exportable NEa/MID audit payload (no secrets)."""
        return {
            "generated_at": dt_util.utcnow().isoformat(),
            "entry_id": self.entry.entry_id,
            "name": self.entry.title,
            "money_unit": "EUR",
            "energy_unit": "kWh",
            "settings": dict(self.settings),
            "daily": self._period_with_profit(self._daily),
            "monthly": self._period_with_profit(self._monthly),
            "current_session": self._session_payload(self._session),
            "last_session": self._last_session,
            "sessions": list(self._sessions),
            "history": dict(self._history),
        }

    # ------------------------------------------------------------------
    # Snapshot and persistence
    # ------------------------------------------------------------------
    def _period_with_profit(self, period: Mapping[str, Any]) -> dict[str, Any]:
        result = {key: round(float(period.get(key, 0.0) or 0.0), 4) for key in PERIOD_KEYS}
        ev_net = (
            result["employer_revenue"]
            + result["ere_revenue"]
            - result["ev_import_cost"]
        )
        arbitrage_profit = (
            result["arbitrage_discharge_value"] - result["arbitrage_charge_cost"]
        )
        result.update(
            {
                "ev_total_revenue": round(
                    result["employer_revenue"] + result["ere_revenue"], 4
                ),
                "ev_net_profit": round(ev_net, 4),
                "arbitrage_profit": round(arbitrage_profit, 4),
                "net_profit": round(
                    calculate_period_profit(
                        result["employer_revenue"],
                        result["ere_revenue"],
                        result["ev_import_cost"],
                        result["arbitrage_discharge_value"],
                        result["arbitrage_charge_cost"],
                    ),
                    4,
                ),
            }
        )
        return result

    @staticmethod
    def _session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
        energy = float(session.get("energy_kwh") or 0.0)
        cost = float(session.get("import_cost") or 0.0)
        employer = float(session.get("employer_revenue") or 0.0)
        fixed = float(session.get("employer_fixed_revenue") or 0.0)
        dynamic = float(session.get("employer_dynamic_revenue") or 0.0)
        ere = float(session.get("ere_revenue") or 0.0)
        return {
            **dict(session),
            "energy_kwh": round(energy, 4),
            "import_cost": round(cost, 4),
            "employer_revenue": round(employer, 4),
            "employer_fixed_revenue": round(fixed, 4),
            "employer_dynamic_revenue": round(dynamic, 4),
            "ere_revenue": round(ere, 4),
            "total_revenue": round(employer + ere, 4),
            "net_profit": round(employer + ere - cost, 4),
            "avg_import_price": round(cost / energy, 4) if energy else 0.0,
        }

    def _system_status(self, readings: Mapping[str, Any]) -> str:
        ev = _as_float(readings.get("ev")) or 0.0
        battery = _as_float(readings.get("battery")) or 0.0
        pv = _as_float(readings.get("pv")) or 0.0
        grid = _as_float(readings.get("grid"))
        if self._modbus_enabled and self._fallback_active:
            return STATUS_FALLBACK
        if self._modbus_enabled and not self._modbus_connected:
            return STATUS_MODBUS_LOST
        if self.modes[MODE_FORCE_GRID_CHARGE]:
            return STATUS_FORCE_GRID
        if self.modes[MODE_FORCE_EV_CHARGING] and ev > 30:
            return STATUS_FORCE_EV
        if ev > 30 or self._ev_actuation == "charging":
            return STATUS_EV_CHARGING
        if battery < -50:
            return STATUS_BATTERY_DISCHARGING
        if self._plan.get("grid_charge"):
            return STATUS_GRID_CHARGING
        if pv > 50 and (grid is None or grid <= 100):
            return STATUS_SOLAR
        if self._modbus_enabled and not self._modbus_write_enabled:
            return STATUS_MONITORING
        return STATUS_IDLE

    def _build_snapshot(self) -> dict[str, Any]:
        readings = self._readings or {}
        now = dt_util.now()
        daily = self._period_with_profit(self._daily)
        monthly = self._period_with_profit(self._monthly)
        session = self._session_payload(self._session)
        battery_soc = _as_float(readings.get("battery_soc"))
        price = _as_float(readings.get("price"))
        lb = self._load_balance
        return {
            "power": {
                "pv": _as_float(readings.get("pv")),
                "grid": _as_float(readings.get("grid")),
                "battery": _as_float(readings.get("battery")),
                "house": _as_float(readings.get("house")),
                "ev": _as_float(readings.get("ev")),
                "heatpump": _as_float(readings.get("heatpump")),
                "house_estimated": bool(readings.get("house_estimated", False)),
            },
            "current": {
                "grid": _as_float(readings.get("grid_current")),
                "available": lb.available_a,
                "max_ev": lb.target_a,
                "other_load": lb.other_load_a,
                "measured": lb.measured,
                "ev_paused": lb.paused,
                "reserve": self._grid_reserve,
            },
            "price": {
                "now": price,
                "bare": _as_float(readings.get("bare_price")),
                "surcharge": _as_float(
                    self.config.get(CONF_PRICE_SURCHARGE), 0.0
                ),
                "prices": self._last_prices,
            },
            "battery": {
                "soc": battery_soc,
                "capacity_kwh": float(
                    _as_float(self.config.get(CONF_BATTERY_CAPACITY), DEFAULT_BATTERY_CAPACITY)
                    or DEFAULT_BATTERY_CAPACITY
                ),
            },
            "ev": {
                "connected": self._ev_connected,
                "desired": self._ev_desired,
                "charging": (_as_float(readings.get("ev")) or 0.0) > 30
                or self._ev_actuation == "charging",
                "paused": lb.paused,
                "target_current": lb.target_a,
                "price_ok": self._ev_price_ok,
                "solar_ok": self._ev_solar_ok,
                "actuation": self._ev_actuation,
                "actuation_error": self._ev_actuation_error,
                "force": self.modes[MODE_FORCE_EV_CHARGING],
            },
            "heatpump": {
                "cop": _as_float(readings.get("heatpump_cop")),
                "power": _as_float(readings.get("heatpump")),
            },
            "ere": {
                "market_price": _as_float(readings.get("ere_market_price")),
                "conversion_factor": _as_float(readings.get("ere_factor")),
                "commission": _as_float(readings.get("ere_commission")),
                "rate_per_kwh": _as_float(readings.get("ere_rate")),
            },
            "financial": {
                "daily": daily,
                "monthly": monthly,
                "session": session,
                "last_session": self._last_session,
                "session_count": len(self._sessions),
            },
            "modbus": {
                "enabled": self._modbus_enabled,
                "write_enabled": self._modbus_write_enabled,
                "connection_type": self._modbus_connection_type,
                "solarman_device": bool(
                    self.config.get(CONF_SOLARMAN_DEVICE_ID)
                ),
                "connected": self._modbus_connected,
                "fallback": self._fallback_active,
                "last_error": self._apply_error or self._connect_error,
                "last_ok": (
                    self._modbus_last_ok.isoformat() if self._modbus_last_ok else None
                ),
                "last_apply": (
                    self._last_apply_at.isoformat() if self._last_apply_at else None
                ),
            },
            "plan": dict(self._plan),
            "modes": dict(self.modes),
            "settings": dict(self.settings),
            "status": self._system_status(readings),
            "tou_slot": tou_slot_for_hour(now.hour, TOU_SLOTS),
            "last_update": now.isoformat(),
            "energy": {
                "daily_grid_import": daily["grid_import_kwh"],
                "daily_grid_export": daily["grid_export_kwh"],
                "daily_pv": daily["pv_energy_kwh"],
                "daily_ev": daily["ev_energy_kwh"],
            },
        }

    def _build_store(self) -> dict[str, Any]:
        return {
            "version": STORAGE_VERSION,
            "daily_date": self._daily_date,
            "monthly_key": self._monthly_key,
            "daily": dict(self._daily),
            "monthly": dict(self._monthly),
            "history": dict(self._history),
            "sessions": list(self._sessions),
            "last_session": self._last_session,
            "session": dict(self._session),
            "session_last_activity": (
                self._session_last_activity.isoformat()
                if self._session_last_activity
                else None
            ),
            "arbitrage_pool_kwh": self._arbitrage_pool_kwh,
            "last_ev_energy": self._last_ev_energy,
            "last_ev_energy_at": (
                self._last_ev_energy_at.isoformat() if self._last_ev_energy_at else None
            ),
            "last_grid_charge_applied": self._last_grid_charge_applied,
            "settings": dict(self.settings),
            "settings_source": dict(self._settings_source),
            "modes": {
                MODE_AUTO_ARBITRAGE: self.modes[MODE_AUTO_ARBITRAGE],
                MODE_AUTO_EV_CHARGING: self.modes[MODE_AUTO_EV_CHARGING],
            },
        }

    def _schedule_save(self) -> None:
        # Keep the delay shorter than the 30 s polling interval, otherwise
        # every new poll re-arms the debounce and the ledger is never written.
        self._store.async_delay_save(self._build_store, 5)

    def _restore(self, stored: Mapping[str, Any]) -> None:
        self._daily_date = stored.get("daily_date") if isinstance(
            stored.get("daily_date"), str
        ) else None
        self._monthly_key = stored.get("monthly_key") if isinstance(
            stored.get("monthly_key"), str
        ) else None
        self._daily = _merge_period(stored.get("daily"), _empty_period())
        self._monthly = _merge_period(stored.get("monthly"), _empty_period())
        history = stored.get("history")
        if isinstance(history, Mapping):
            for day, period in history.items():
                if isinstance(day, str) and isinstance(period, Mapping):
                    self._history[day] = _merge_period(period, _empty_period())
            if len(self._history) > MAX_HISTORY_DAYS:
                for key in sorted(self._history)[: len(self._history) - MAX_HISTORY_DAYS]:
                    del self._history[key]
        sessions = stored.get("sessions")
        if isinstance(sessions, list):
            self._sessions = [dict(item) for item in sessions if isinstance(item, Mapping)]
            self._sessions = self._sessions[-MAX_STORED_SESSIONS:]
        last_session = stored.get("last_session")
        if isinstance(last_session, Mapping):
            self._last_session = dict(last_session)
        session = stored.get("session")
        if isinstance(session, Mapping):
            restored = _empty_session()
            restored.update(
                {
                    key: session[key]
                    for key in restored
                    if key in session
                }
            )
            for key in ("energy_kwh", "import_cost", "employer_revenue", "ere_revenue"):
                value = _as_float(restored.get(key))
                restored[key] = value if value is not None else 0.0
            self._session = restored
        activity = stored.get("session_last_activity")
        if isinstance(activity, str):
            self._session_last_activity = self._parse_dt(activity)
        pool = _as_float(stored.get("arbitrage_pool_kwh"))
        if pool is not None and pool >= 0:
            self._arbitrage_pool_kwh = pool
        last_energy = _as_float(stored.get("last_ev_energy"))
        if last_energy is not None:
            self._last_ev_energy = last_energy
        meter_at = stored.get("last_ev_energy_at")
        if isinstance(meter_at, str):
            self._last_ev_energy_at = self._parse_dt(meter_at)
        self._last_grid_charge_applied = bool(
            stored.get("last_grid_charge_applied", False)
        )

        stored_settings = stored.get("settings")
        stored_source = stored.get("settings_source")
        if isinstance(stored_settings, Mapping):
            for key, limits in SETTING_LIMITS.items():
                value = _as_float(stored_settings.get(key))
                if value is None:
                    continue
                source = _as_float(stored_source.get(key)) if isinstance(
                    stored_source, Mapping
                ) else None
                current_option = self._settings_source.get(key)
                # A store written before settings_source existed must let the
                # configured option win once. For new stores, a changed option
                # wins and an unchanged option keeps the dashboard number.
                if source is None:
                    if current_option is not None:
                        value = current_option
                elif current_option is not None and abs(
                    source - current_option
                ) > 1e-9:
                    value = current_option
                self.settings[key] = max(limits[0], min(limits[1], value))

        stored_modes = stored.get("modes")
        if isinstance(stored_modes, Mapping):
            for key in (MODE_AUTO_ARBITRAGE, MODE_AUTO_EV_CHARGING):
                if isinstance(stored_modes.get(key), bool):
                    self.modes[key] = stored_modes[key]
        # Force modes must never survive a restart.
        self.modes[MODE_FORCE_EV_CHARGING] = False
        self.modes[MODE_FORCE_GRID_CHARGE] = False
