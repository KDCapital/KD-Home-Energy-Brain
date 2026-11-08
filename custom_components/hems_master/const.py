"""Constants for the HEMS Master integration.

The module intentionally has no Home Assistant imports so the financial and
load-balancing logic can be unit-tested without a Home Assistant runtime.
"""

from __future__ import annotations

DOMAIN = "hems_master"
NAME = "HEMS Master"
DEVICE_NAME = "HEMS"
VERSION = "1.0.0"
ISSUE_URL = "https://github.com/larsk/hems_master/issues"

# Platforms are strings to keep this module importable outside Home Assistant.
PLATFORMS = ["sensor", "binary_sensor", "switch", "number"]

# ---------------------------------------------------------------------------
# Config entry keys
# ---------------------------------------------------------------------------
CONF_NAME = "name"

CONF_PRICE_ENTITY = "price_entity"
CONF_BARE_PRICE_ENTITY = "bare_price_entity"
CONF_EXPORT_PRICE_ENTITY = "export_price_entity"
CONF_PRICE_ATTRIBUTE = "price_attribute"
CONF_PRICE_FORECAST_ENTITY = "price_forecast_entity"
CONF_PRICE_ENTITY_IS_BARE = "price_entity_is_bare"
CONF_BATTERY_TRADING_ENABLED = "battery_trading_enabled"
CONF_BATTERY_TRADE_MIN_SPREAD = "battery_trade_min_spread"
CONF_EV_ENERGY_ENTITY = "ev_energy_entity"
CONF_EV_POWER_ENTITY = "ev_power_entity"
CONF_EV_CURRENT_ENTITY = "ev_current_entity"
CONF_EV_CONNECTED_ENTITY = "ev_connected_entity"
CONF_EV_CHARGER_SWITCH_ENTITY = "ev_charger_switch_entity"
CONF_EV_CURRENT_NUMBER_ENTITY = "ev_current_number_entity"
CONF_EV_MODE_SELECT_ENTITY = "ev_mode_select_entity"
CONF_EV_MODE_OFF = "ev_mode_off"
CONF_EV_MODE_SOLAR = "ev_mode_solar"
CONF_EV_MODE_MIN_SOLAR = "ev_mode_min_solar"
CONF_EV_MODE_FAST = "ev_mode_fast"

CONF_BATTERY_POWER_ENTITY = "battery_power_entity"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_GRID_CURRENT_ENTITY = "grid_current_entity"
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"
CONF_HEATPUMP_POWER_ENTITY = "heatpump_power_entity"
CONF_HEATPUMP_COP_ENTITY = "heatpump_cop_entity"

CONF_EMPLOYER_RATE = "employer_rate"
CONF_ERE_RATE = "ere_rate"
CONF_ERE_MARKET_PRICE_ENTITY = "ere_market_price_entity"
CONF_ERE_CONVERSION_FACTOR = "ere_conversion_factor"
CONF_ERE_COMMISSION = "ere_commission"
CONF_MIN_ARBITRAGE_MARGIN = "min_arbitrage_margin"
CONF_MAX_GRID_CURRENT = "max_grid_current"
CONF_PRICE_SURCHARGE = "price_surcharge"
CONF_EMPLOYER_PAYS_DYNAMIC = "employer_pays_dynamic"

CONF_PHASES = "phases"
CONF_VOLTAGE = "voltage"
CONF_MIN_EV_CURRENT = "min_ev_current"
CONF_MAX_EV_CURRENT = "max_ev_current"
CONF_GRID_CURRENT_RESERVE = "grid_current_reserve"
CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_ROUND_TRIP_EFFICIENCY = "round_trip_efficiency"

CONF_GRID_POWER_INVERTED = "grid_power_inverted"
CONF_BATTERY_POWER_INVERTED = "battery_power_inverted"
CONF_EV_CONTROL_ENABLED = "ev_control_enabled"

DEFAULT_EV_CONTROL_ENABLED = True

CONF_MODBUS_ENABLED = "modbus_enabled"
CONF_MODBUS_WRITE_ENABLED = "modbus_write_enabled"
CONF_MODBUS_CONNECTION_TYPE = "modbus_connection_type"
CONF_SOLARMAN_DEVICE_ID = "solarman_device_id"
CONF_FRANK_DEVICE_ID = "frank_device_id"
CONF_HOMEWIZARD_DEVICE_ID = "homewizard_device_id"
CONF_EVCC_DEVICE_ID = "evcc_device_id"
CONF_JOULO_DEVICE_ID = "joulo_device_id"
CONF_MODBUS_HOST = "modbus_host"
CONF_MODBUS_PORT = "modbus_port"
CONF_MODBUS_UNIT = "modbus_unit"

MODBUS_CONNECTION_SOLARMAN = "solarman"
MODBUS_CONNECTION_TCP = "tcp"
DEFAULT_MODBUS_CONNECTION_TYPE = MODBUS_CONNECTION_SOLARMAN

# Optional entity keys stored in the config entry and cleaned by the flows.
# The required price and EV MID-meter entities are handled separately so that
# opening the optional-mapping step cannot erase them.
ENTITY_CONFIG_KEYS = (
    CONF_EV_POWER_ENTITY,
    CONF_EV_CURRENT_ENTITY,
    CONF_EV_CONNECTED_ENTITY,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CURRENT_NUMBER_ENTITY,
    CONF_EV_MODE_SELECT_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_CURRENT_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HEATPUMP_POWER_ENTITY,
    CONF_HEATPUMP_COP_ENTITY,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_NAME = NAME
DEFAULT_EMPLOYER_RATE = 0.25
DEFAULT_ERE_RATE = 0.124
DEFAULT_ERE_CONVERSION_FACTOR = 0.332694
DEFAULT_ERE_COMMISSION = 0.0
DEFAULT_MIN_ARBITRAGE_MARGIN = 0.08
DEFAULT_MAX_GRID_CURRENT = 25
DEFAULT_PRICE_SURCHARGE = 0.25
DEFAULT_EMPLOYER_PAYS_DYNAMIC = True
DEFAULT_PRICE_ENTITY_IS_BARE = False
DEFAULT_BATTERY_TRADING_ENABLED = False
DEFAULT_BATTERY_TRADE_MIN_SPREAD = 0.05
DEFAULT_PHASES = 3
DEFAULT_VOLTAGE = 230
DEFAULT_MIN_EV_CURRENT = 6
DEFAULT_MAX_EV_CURRENT = 16
DEFAULT_GRID_CURRENT_RESERVE = 1.0
DEFAULT_BATTERY_CAPACITY = 32.0
DEFAULT_ROUND_TRIP_EFFICIENCY = 0.90
DEFAULT_EV_MODE_OFF = "Off"
DEFAULT_EV_MODE_SOLAR = "Solar"
DEFAULT_EV_MODE_MIN_SOLAR = "Min+Solar"
DEFAULT_EV_MODE_FAST = "Fast"

DEFAULT_SCAN_INTERVAL = 30
LOAD_BALANCE_INTERVAL = 5
PLAN_INTERVAL = 60
MODBUS_TIMEOUT = 5
MODBUS_BAUDRATE = 9600

SESSION_GAP_SECONDS = 30 * 60
MAX_ACCEPTABLE_METER_JUMP_KWH = 250.0
MAX_RESTORE_GAP_DAYS = 7
MAX_HISTORY_DAYS = 400
MAX_STORED_SESSIONS = 100

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.ledger"

# ---------------------------------------------------------------------------
# Deye Modbus registers from the project specification
# ---------------------------------------------------------------------------
REG_UNLOCK = 1100
REG_GRID_CHARGE = 130
REG_SOLAR_SELL_SLOT_1 = 145
REG_TARGET_SOC_SLOT_1 = 166
REG_CHARGE_ENABLE_SLOT_1 = 172
REG_SYSTEM_TIME_MONTH_YEAR = 62
REG_SYSTEM_TIME_DAY_HOUR = 63
REG_SYSTEM_TIME_MINUTE_SECOND = 64
REG_ZERO_EXPORT_POWER = 104
REG_MAX_CHARGE_CURRENT = 108
REG_MAX_DISCHARGE_CURRENT = 109

MODBUS_UNLOCK_VALUE = 1
MODBUS_LOCK_VALUE = 0

# Deye uses 03H for reads and 10H for writes. pymodbus exposes those as
# read_holding_registers() and write_register().
MODBUS_READ_FUNCTION = 3
MODBUS_WRITE_FUNCTION = 16
MODBUS_EXCEPTION_BASE = 0x80

# ---------------------------------------------------------------------------
# TOU slots from the specification. Only used for status/context; the
# integration deliberately does not rewrite the inverter clock or slot times.
# ---------------------------------------------------------------------------
TOU_SLOTS = (
    {"slot": 1, "start": "00:00", "end": "06:00", "name": "Night"},
    {"slot": 2, "start": "06:00", "end": "10:00", "name": "Morning"},
    {"slot": 3, "start": "10:00", "end": "14:00", "name": "Midday"},
    {"slot": 4, "start": "14:00", "end": "17:00", "name": "Afternoon"},
    {"slot": 5, "start": "17:00", "end": "21:00", "name": "Evening"},
    {"slot": 6, "start": "21:00", "end": "00:00", "name": "Late evening"},
)

# ---------------------------------------------------------------------------
# Modes, status values and plan reasons
# ---------------------------------------------------------------------------
MODE_AUTO_ARBITRAGE = "auto_arbitrage"
MODE_AUTO_EV_CHARGING = "auto_ev_charging"
MODE_FORCE_EV_CHARGING = "force_ev_charging"
MODE_FORCE_GRID_CHARGE = "force_grid_charge"

DEFAULT_MODES = {
    MODE_AUTO_ARBITRAGE: True,
    MODE_AUTO_EV_CHARGING: True,
    MODE_FORCE_EV_CHARGING: False,
    MODE_FORCE_GRID_CHARGE: False,
}

STATUS_MONITORING = "Monitoring only"
STATUS_MODBUS_LOST = "Modbus communication lost"
STATUS_FALLBACK = "Fallback - inverter internal TOU"
STATUS_FORCE_GRID = "Grid Charging (forced)"
STATUS_FORCE_EV = "EV Charging (forced)"
STATUS_EV_CHARGING = "EV Charging"
STATUS_BATTERY_DISCHARGING = "Battery Discharging"
STATUS_GRID_CHARGING = "Grid Charging"
STATUS_SOLAR = "Solar Self-Consumption"
STATUS_IDLE = "Idle"

STATUS_OPTIONS = [
    STATUS_MONITORING,
    STATUS_MODBUS_LOST,
    STATUS_FALLBACK,
    STATUS_FORCE_GRID,
    STATUS_FORCE_EV,
    STATUS_EV_CHARGING,
    STATUS_BATTERY_DISCHARGING,
    STATUS_GRID_CHARGING,
    STATUS_SOLAR,
    STATUS_IDLE,
]

REASON_FORCED = "forced"
REASON_NEGATIVE_PRICE = "negative_or_zero_price"
REASON_SPREAD = "profitable_price_spread"
REASON_FULL = "battery_full"
REASON_PEAK = "high_price_discharge"
REASON_SELF_CONSUMPTION = "self_consumption"
REASON_NO_DATA = "insufficient_data"
REASON_AUTO_OFF = "automatic_control_off"
REASON_WRITES_DISABLED = "modbus_writes_disabled"
REASON_FALLBACK = "communication_fallback"

# ---------------------------------------------------------------------------
# Service names
# ---------------------------------------------------------------------------
SERVICE_RECALCULATE = "recalculate"
SERVICE_RESET_TODAY = "reset_today"
SERVICE_APPLY_PLAN = "apply_plan"
SERVICE_EXPORT_AUDIT = "export_audit"
