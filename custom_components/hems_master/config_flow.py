"""Config and options flow for HEMS Master."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_INVERTED,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_TRADE_MIN_SPREAD,
    CONF_BATTERY_TRADING_ENABLED,
    CONF_BARE_PRICE_ENTITY,
    CONF_EMPLOYER_RATE,
    CONF_EMPLOYER_PAYS_DYNAMIC,
    CONF_ERE_COMMISSION,
    CONF_ERE_CONVERSION_FACTOR,
    CONF_ERE_MARKET_PRICE_ENTITY,
    CONF_ERE_RATE,
    CONF_EXPORT_PRICE_ENTITY,
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
    CONF_EVCC_DEVICE_ID,
    CONF_FRANK_DEVICE_ID,
    CONF_GRID_CURRENT_ENTITY,
    CONF_GRID_CURRENT_RESERVE,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HEATPUMP_COP_ENTITY,
    CONF_HEATPUMP_POWER_ENTITY,
    CONF_HOMEWIZARD_DEVICE_ID,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_JOULO_DEVICE_ID,
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
    CONF_NAME,
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
    DEFAULT_BATTERY_TRADE_MIN_SPREAD,
    DEFAULT_BATTERY_TRADING_ENABLED,
    DEFAULT_EMPLOYER_PAYS_DYNAMIC,
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
    DEFAULT_MODBUS_CONNECTION_TYPE,
    DEFAULT_NAME,
    DEFAULT_PHASES,
    DEFAULT_PRICE_ENTITY_IS_BARE,
    DEFAULT_PRICE_SURCHARGE,
    DEFAULT_ROUND_TRIP_EFFICIENCY,
    DEFAULT_VOLTAGE,
    DOMAIN,
    ENTITY_CONFIG_KEYS,
    MODBUS_CONNECTION_SOLARMAN,
    MODBUS_CONNECTION_TCP,
)
from .modbus_client import DeyeModbusClient
from .solarman_client import (
    DeyeSolarmanClient,
    solarman_available,
    solarman_write_available,
)
from .auto_detect import auto_detect_sources

HOST_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")
_LOGGER = logging.getLogger(__name__)


def _entity_selector(domain: str) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _optional_entity(key: str, domain: str, current: Any) -> Any:
    return vol.Optional(
        key,
        description={"suggested_value": current if current else None},
    )


def _number(
    minimum: float,
    maximum: float,
    step: float,
    *,
    unit: str | None = None,
) -> selector.NumberSelector:
    config_kwargs: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config_kwargs["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config_kwargs))


def _entity_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _optional_entity(
                CONF_EV_POWER_ENTITY, "sensor", config.get(CONF_EV_POWER_ENTITY)
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_EV_CURRENT_ENTITY, "sensor", config.get(CONF_EV_CURRENT_ENTITY)
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_EV_CONNECTED_ENTITY,
                "binary_sensor",
                config.get(CONF_EV_CONNECTED_ENTITY),
            ): _entity_selector("binary_sensor"),
            _optional_entity(
                CONF_EV_CHARGER_SWITCH_ENTITY,
                "switch",
                config.get(CONF_EV_CHARGER_SWITCH_ENTITY),
            ): _entity_selector("switch"),
            _optional_entity(
                CONF_EV_CURRENT_NUMBER_ENTITY,
                "number",
                config.get(CONF_EV_CURRENT_NUMBER_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "select"])
            ),
            _optional_entity(
                CONF_EV_MODE_SELECT_ENTITY,
                "select",
                config.get(CONF_EV_MODE_SELECT_ENTITY),
            ): _entity_selector("select"),
            _optional_entity(
                CONF_BATTERY_POWER_ENTITY,
                "sensor",
                config.get(CONF_BATTERY_POWER_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_BATTERY_SOC_ENTITY,
                "sensor",
                config.get(CONF_BATTERY_SOC_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_GRID_POWER_ENTITY,
                "sensor",
                config.get(CONF_GRID_POWER_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_GRID_CURRENT_ENTITY,
                "sensor",
                config.get(CONF_GRID_CURRENT_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_PV_POWER_ENTITY, "sensor", config.get(CONF_PV_POWER_ENTITY)
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_HOUSE_LOAD_ENTITY,
                "sensor",
                config.get(CONF_HOUSE_LOAD_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_HEATPUMP_POWER_ENTITY,
                "sensor",
                config.get(CONF_HEATPUMP_POWER_ENTITY),
            ): _entity_selector("sensor"),
            _optional_entity(
                CONF_HEATPUMP_COP_ENTITY,
                "sensor",
                config.get(CONF_HEATPUMP_COP_ENTITY),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_EV_MODE_OFF,
                default=str(config.get(CONF_EV_MODE_OFF, DEFAULT_EV_MODE_OFF)),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_EV_MODE_SOLAR,
                default=str(config.get(CONF_EV_MODE_SOLAR, DEFAULT_EV_MODE_SOLAR)),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_EV_MODE_MIN_SOLAR,
                default=str(
                    config.get(CONF_EV_MODE_MIN_SOLAR, DEFAULT_EV_MODE_MIN_SOLAR)
                ),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_EV_MODE_FAST,
                default=str(config.get(CONF_EV_MODE_FAST, DEFAULT_EV_MODE_FAST)),
            ): selector.TextSelector(),
        }
    )


def _finance_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_PRICE_ENTITY,
                description={
                    "suggested_value": config.get(CONF_PRICE_ENTITY) or None
                },
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_BARE_PRICE_ENTITY,
                description={
                    "suggested_value": config.get(CONF_BARE_PRICE_ENTITY) or None
                },
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_PRICE_ATTRIBUTE,
                default=str(config.get(CONF_PRICE_ATTRIBUTE) or ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_PRICE_FORECAST_ENTITY,
                description={
                    "suggested_value": config.get(CONF_PRICE_FORECAST_ENTITY)
                    or None
                },
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_EXPORT_PRICE_ENTITY,
                description={
                    "suggested_value": config.get(CONF_EXPORT_PRICE_ENTITY)
                    or None
                },
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_BATTERY_TRADING_ENABLED,
                default=bool(
                    config.get(
                        CONF_BATTERY_TRADING_ENABLED,
                        DEFAULT_BATTERY_TRADING_ENABLED,
                    )
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_BATTERY_TRADE_MIN_SPREAD,
                default=float(
                    config.get(
                        CONF_BATTERY_TRADE_MIN_SPREAD,
                        DEFAULT_BATTERY_TRADE_MIN_SPREAD,
                    )
                ),
            ): _number(0, 1, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_EMPLOYER_RATE,
                default=float(config.get(CONF_EMPLOYER_RATE, DEFAULT_EMPLOYER_RATE)),
            ): _number(0, 5, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_ERE_RATE,
                default=float(config.get(CONF_ERE_RATE, DEFAULT_ERE_RATE)),
            ): _number(0, 2, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_ERE_MARKET_PRICE_ENTITY,
                description={
                    "suggested_value": config.get(CONF_ERE_MARKET_PRICE_ENTITY)
                    or None
                },
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_ERE_CONVERSION_FACTOR,
                default=float(
                    config.get(
                        CONF_ERE_CONVERSION_FACTOR, DEFAULT_ERE_CONVERSION_FACTOR
                    )
                ),
            ): _number(0, 2, 0.001),
            vol.Optional(
                CONF_ERE_COMMISSION,
                default=float(
                    config.get(CONF_ERE_COMMISSION, DEFAULT_ERE_COMMISSION)
                ),
            ): _number(0, 100, 1),
            vol.Optional(
                CONF_MIN_ARBITRAGE_MARGIN,
                default=float(
                    config.get(CONF_MIN_ARBITRAGE_MARGIN, DEFAULT_MIN_ARBITRAGE_MARGIN)
                ),
            ): _number(0, 1, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_PRICE_SURCHARGE,
                default=float(
                    config.get(CONF_PRICE_SURCHARGE, DEFAULT_PRICE_SURCHARGE)
                ),
            ): _number(0, 2, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_PRICE_ENTITY_IS_BARE,
                default=bool(
                    config.get(
                        CONF_PRICE_ENTITY_IS_BARE, DEFAULT_PRICE_ENTITY_IS_BARE
                    )
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_EMPLOYER_PAYS_DYNAMIC,
                default=bool(
                    config.get(
                        CONF_EMPLOYER_PAYS_DYNAMIC, DEFAULT_EMPLOYER_PAYS_DYNAMIC
                    )
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_BATTERY_CAPACITY,
                default=float(
                    config.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)
                ),
            ): _number(1, 200, 0.1, unit="kWh"),
            vol.Optional(
                CONF_ROUND_TRIP_EFFICIENCY,
                default=float(
                    config.get(CONF_ROUND_TRIP_EFFICIENCY, DEFAULT_ROUND_TRIP_EFFICIENCY)
                ),
            ): _number(0.5, 1.0, 0.01),
        }
    )


def _grid_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_PHASES, default=int(config.get(CONF_PHASES, DEFAULT_PHASES))
            ): _number(1, 3, 1),
            vol.Optional(
                CONF_VOLTAGE, default=float(config.get(CONF_VOLTAGE, DEFAULT_VOLTAGE))
            ): _number(100, 300, 1, unit="V"),
            vol.Optional(
                CONF_MAX_GRID_CURRENT,
                default=float(config.get(CONF_MAX_GRID_CURRENT, DEFAULT_MAX_GRID_CURRENT)),
            ): _number(1, 125, 1, unit="A"),
            vol.Optional(
                CONF_MIN_EV_CURRENT,
                default=float(config.get(CONF_MIN_EV_CURRENT, DEFAULT_MIN_EV_CURRENT)),
            ): _number(6, 80, 1, unit="A"),
            vol.Optional(
                CONF_MAX_EV_CURRENT,
                default=float(config.get(CONF_MAX_EV_CURRENT, DEFAULT_MAX_EV_CURRENT)),
            ): _number(6, 80, 1, unit="A"),
            vol.Optional(
                CONF_GRID_CURRENT_RESERVE,
                default=float(
                    config.get(CONF_GRID_CURRENT_RESERVE, DEFAULT_GRID_CURRENT_RESERVE)
                ),
            ): _number(0, 10, 0.1, unit="A"),
            vol.Optional(
                CONF_EV_CONTROL_ENABLED,
                default=bool(
                    config.get(CONF_EV_CONTROL_ENABLED, DEFAULT_EV_CONTROL_ENABLED)
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_GRID_POWER_INVERTED,
                default=bool(config.get(CONF_GRID_POWER_INVERTED, False)),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_BATTERY_POWER_INVERTED,
                default=bool(config.get(CONF_BATTERY_POWER_INVERTED, False)),
            ): selector.BooleanSelector(),
        }
    )


def _source_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_SOLARMAN_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_SOLARMAN_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="solarman")
            ),
            vol.Optional(
                CONF_FRANK_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_FRANK_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="frank_quarter_prices")
            ),
            vol.Optional(
                CONF_HOMEWIZARD_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_HOMEWIZARD_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="homewizard")
            ),
            vol.Optional(
                CONF_EVCC_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_EVCC_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="evcc_intg")
            ),
            vol.Optional(
                CONF_JOULO_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_JOULO_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="joulo")
            ),
        }
    )


def _detected_sources(data: dict[str, Any]) -> dict[str, str | None]:
    return {
        "solarman": data.get(CONF_SOLARMAN_DEVICE_ID),
        "frank_quarter_prices": data.get(CONF_FRANK_DEVICE_ID),
        "homewizard": data.get(CONF_HOMEWIZARD_DEVICE_ID),
        "evcc_intg": data.get(CONF_EVCC_DEVICE_ID),
        "joulo": data.get(CONF_JOULO_DEVICE_ID),
    }


def _modbus_schema(config: dict[str, Any]) -> vol.Schema:
    configured_connection_type = config.get(CONF_MODBUS_CONNECTION_TYPE)
    if configured_connection_type is None:
        configured_connection_type = (
            MODBUS_CONNECTION_TCP
            if config.get(CONF_MODBUS_HOST)
            else DEFAULT_MODBUS_CONNECTION_TYPE
        )
    return vol.Schema(
        {
            vol.Optional(
                CONF_MODBUS_ENABLED,
                default=bool(config.get(CONF_MODBUS_ENABLED, False)),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MODBUS_CONNECTION_TYPE,
                default=str(configured_connection_type),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "value": MODBUS_CONNECTION_SOLARMAN,
                            "label": "Solarman integration (recommended)",
                        },
                        {
                            "value": MODBUS_CONNECTION_TCP,
                            "label": "Direct Modbus TCP",
                        },
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_SOLARMAN_DEVICE_ID,
                description={
                    "suggested_value": config.get(CONF_SOLARMAN_DEVICE_ID) or None
                },
            ): selector.DeviceSelector(
                selector.DeviceSelectorConfig(integration="solarman")
            ),
            vol.Optional(
                CONF_MODBUS_HOST,
                default=str(config.get(CONF_MODBUS_HOST, "")),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_MODBUS_PORT, default=int(config.get(CONF_MODBUS_PORT, 502))
            ): _number(1, 65535, 1),
            vol.Optional(
                CONF_MODBUS_UNIT, default=int(config.get(CONF_MODBUS_UNIT, 1))
            ): _number(1, 247, 1),
            vol.Optional(
                CONF_MODBUS_WRITE_ENABLED,
                default=bool(config.get(CONF_MODBUS_WRITE_ENABLED, False)),
            ): selector.BooleanSelector(),
        }
    )


def _clean_entities(data: dict[str, Any]) -> dict[str, Any]:
    for key in ENTITY_CONFIG_KEYS:
        value = data.get(key)
        data[key] = str(value).strip() if value else None
    return data


async def _test_modbus(host: str, port: int, unit: int) -> None:
    client = DeyeModbusClient(host=host, port=port, slave_id=unit)
    try:
        await client.async_test_connection()
    finally:
        await client.async_close()


async def _validate_modbus(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> str | None:
    """Validate the selected Deye transport and return a translation error key."""
    if not bool(user_input.get(CONF_MODBUS_ENABLED, False)):
        return None
    connection_type = str(
        user_input.get(CONF_MODBUS_CONNECTION_TYPE, DEFAULT_MODBUS_CONNECTION_TYPE)
    )
    if connection_type == MODBUS_CONNECTION_SOLARMAN:
        device_id = str(user_input.get(CONF_SOLARMAN_DEVICE_ID) or "").strip()
        if not device_id:
            return "solarman_device_required"
        if not solarman_available(hass):
            return "solarman_not_configured"
        if bool(user_input.get(CONF_MODBUS_WRITE_ENABLED, False)) and not solarman_write_available(
            hass
        ):
            return "solarman_write_unavailable"
        try:
            async with asyncio.timeout(12):
                client = DeyeSolarmanClient(hass, device_id)
                await client.async_test_connection()
        except Exception as err:  # noqa: BLE001 - reported as a form error
            _LOGGER.warning("Solarman connection test failed: %s", err)
            return "cannot_connect"
        return None

    host = str(user_input.get(CONF_MODBUS_HOST, "")).strip()
    if not host or not HOST_RE.fullmatch(host):
        return "invalid_host"
    try:
        async with asyncio.timeout(12):
            await _test_modbus(
                host,
                int(user_input.get(CONF_MODBUS_PORT, 502)),
                int(user_input.get(CONF_MODBUS_UNIT, 1)),
            )
    except Exception:  # noqa: BLE001
        return "cannot_connect"
    return None


class HEMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Multi-step UI setup for the HEMS Master integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        errors: dict[str, str] = {}
        if user_input is not None:
            price = str(user_input.get(CONF_PRICE_ENTITY) or "").strip()
            ev_energy = str(user_input.get(CONF_EV_ENERGY_ENTITY) or "").strip()
            if not price or self.hass.states.get(price) is None:
                errors["base"] = "entity_not_found"
            elif not ev_energy or self.hass.states.get(ev_energy) is None:
                errors["base"] = "entity_not_found"
            else:
                self._data.update(user_input)
                self._data[CONF_NAME] = str(
                    user_input.get(CONF_NAME) or DEFAULT_NAME
                ).strip()
                self._data[CONF_PRICE_ENTITY] = price
                self._data[CONF_EV_ENERGY_ENTITY] = ev_energy
                return await self.async_step_sources()
        form_data = user_input or self._data
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NAME,
                    default=str(form_data.get(CONF_NAME) or DEFAULT_NAME),
                ): selector.TextSelector(),
                vol.Required(
                    CONF_PRICE_ENTITY,
                    description={
                        "suggested_value": form_data.get(CONF_PRICE_ENTITY) or None
                    },
                ): _entity_selector("sensor"),
                vol.Required(
                    CONF_EV_ENERGY_ENTITY,
                    description={
                        "suggested_value": form_data.get(CONF_EV_ENERGY_ENTITY) or None
                    },
                ): _entity_selector("sensor"),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            detected = auto_detect_sources(
                self.hass, _detected_sources({**self._data, **user_input})
            )
            self._data.update(detected)
            return await self.async_step_entities()
        return self.async_show_form(
            step_id="sources",
            data_schema=_source_schema(self._data),
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean_entities(dict(user_input)))
            return await self.async_step_finance()
        return self.async_show_form(
            step_id="entities",
            data_schema=_entity_schema(self._data),
        )

    async def async_step_finance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_grid()
        return self.async_show_form(step_id="finance", data_schema=_finance_schema(self._data))

    async def async_step_grid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_modbus()
        return self.async_show_form(step_id="grid", data_schema=_grid_schema(self._data))

    async def async_step_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            modbus_enabled = bool(user_input.get(CONF_MODBUS_ENABLED, False))
            write_enabled = bool(user_input.get(CONF_MODBUS_WRITE_ENABLED, False))
            host = str(user_input.get(CONF_MODBUS_HOST, "")).strip()
            connection_type = str(
                user_input.get(
                    CONF_MODBUS_CONNECTION_TYPE, DEFAULT_MODBUS_CONNECTION_TYPE
                )
            )
            device_id = str(user_input.get(CONF_SOLARMAN_DEVICE_ID) or "").strip()
            error = await _validate_modbus(self.hass, user_input)
            if error is not None:
                errors["base"] = error
            if not errors:
                self._data.update(user_input)
                self._data[CONF_MODBUS_ENABLED] = modbus_enabled
                self._data[CONF_MODBUS_WRITE_ENABLED] = bool(
                    modbus_enabled and write_enabled
                )
                self._data[CONF_MODBUS_CONNECTION_TYPE] = connection_type
                self._data[CONF_SOLARMAN_DEVICE_ID] = device_id or None
                self._data[CONF_MODBUS_HOST] = host
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(self._data.get(CONF_NAME) or DEFAULT_NAME),
                    data=self._data,
                )
        return self.async_show_form(
            step_id="modbus",
            data_schema=_modbus_schema({**self._data, **user_input} if user_input else self._data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HEMSOptionsFlow()


class HEMSOptionsFlow(OptionsFlow):
    """Edit mappings and parameters after setup, without helpers."""

    def _merged(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        # Keep entry.data as the immutable base and store only option
        # overrides. Writing the full merged config here would permanently
        # shadow future data migrations.
        merged = {**self.config_entry.options, **data}
        return self.async_create_entry(title="", data=merged)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["sources", "entities", "finance", "grid", "modbus"],
        )

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            detected = auto_detect_sources(
                self.hass, _detected_sources({**self._merged(), **user_input})
            )
            return self._finish({**user_input, **detected})
        return self.async_show_form(
            step_id="sources", data_schema=_source_schema(self._merged())
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._finish(_clean_entities(dict(user_input)))
        return self.async_show_form(
            step_id="entities", data_schema=_entity_schema(self._merged())
        )

    async def async_step_finance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="finance", data_schema=_finance_schema(self._merged())
        )

    async def async_step_grid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="grid", data_schema=_grid_schema(self._merged())
        )

    async def async_step_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            modbus_enabled = bool(user_input.get(CONF_MODBUS_ENABLED, False))
            write_enabled = bool(user_input.get(CONF_MODBUS_WRITE_ENABLED, False))
            host = str(user_input.get(CONF_MODBUS_HOST, "")).strip()
            connection_type = str(
                user_input.get(
                    CONF_MODBUS_CONNECTION_TYPE, DEFAULT_MODBUS_CONNECTION_TYPE
                )
            )
            device_id = str(user_input.get(CONF_SOLARMAN_DEVICE_ID) or "").strip()
            error = await _validate_modbus(self.hass, user_input)
            if error is not None:
                errors["base"] = error
            if not errors:
                user_input[CONF_MODBUS_ENABLED] = modbus_enabled
                user_input[CONF_MODBUS_WRITE_ENABLED] = bool(
                    modbus_enabled and write_enabled
                )
                user_input[CONF_MODBUS_CONNECTION_TYPE] = connection_type
                user_input[CONF_SOLARMAN_DEVICE_ID] = device_id or None
                user_input[CONF_MODBUS_HOST] = host
                return self._finish(user_input)
        return self.async_show_form(
            step_id="modbus",
            data_schema=_modbus_schema({**self._merged(), **user_input} if user_input else self._merged()),
            errors=errors,
        )
