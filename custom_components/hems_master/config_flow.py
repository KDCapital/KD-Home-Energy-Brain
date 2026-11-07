"""Config and options flow for HEMS Master."""

from __future__ import annotations

import asyncio
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
    CONF_EMPLOYER_RATE,
    CONF_ERE_RATE,
    CONF_EV_CHARGER_SWITCH_ENTITY,
    CONF_EV_CONNECTED_ENTITY,
    CONF_EV_CONTROL_ENABLED,
    CONF_EV_CURRENT_ENTITY,
    CONF_EV_CURRENT_NUMBER_ENTITY,
    CONF_EV_ENERGY_ENTITY,
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
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT,
    CONF_MODBUS_WRITE_ENABLED,
    CONF_NAME,
    CONF_PHASES,
    CONF_PRICE_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_VOLTAGE,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_EMPLOYER_RATE,
    DEFAULT_ERE_RATE,
    DEFAULT_EV_CONTROL_ENABLED,
    DEFAULT_GRID_CURRENT_RESERVE,
    DEFAULT_MAX_EV_CURRENT,
    DEFAULT_MAX_GRID_CURRENT,
    DEFAULT_MIN_ARBITRAGE_MARGIN,
    DEFAULT_MIN_EV_CURRENT,
    DEFAULT_NAME,
    DEFAULT_PHASES,
    DEFAULT_ROUND_TRIP_EFFICIENCY,
    DEFAULT_VOLTAGE,
    DOMAIN,
    ENTITY_CONFIG_KEYS,
)
from .modbus_client import DeyeModbusClient

HOST_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")


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
            ): _entity_selector("number"),
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
        }
    )


def _finance_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_EMPLOYER_RATE,
                default=float(config.get(CONF_EMPLOYER_RATE, DEFAULT_EMPLOYER_RATE)),
            ): _number(0, 5, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_ERE_RATE,
                default=float(config.get(CONF_ERE_RATE, DEFAULT_ERE_RATE)),
            ): _number(0, 2, 0.001, unit="EUR/kWh"),
            vol.Optional(
                CONF_MIN_ARBITRAGE_MARGIN,
                default=float(
                    config.get(CONF_MIN_ARBITRAGE_MARGIN, DEFAULT_MIN_ARBITRAGE_MARGIN)
                ),
            ): _number(0, 1, 0.001, unit="EUR/kWh"),
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


def _modbus_schema(config: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_MODBUS_ENABLED,
                default=bool(config.get(CONF_MODBUS_ENABLED, False)),
            ): selector.BooleanSelector(),
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
                return await self.async_step_entities()
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
            if modbus_enabled:
                if not host or not HOST_RE.fullmatch(host):
                    errors["base"] = "invalid_host"
                else:
                    try:
                        async with asyncio.timeout(12):
                            await _test_modbus(
                                host,
                                int(user_input.get(CONF_MODBUS_PORT, 502)),
                                int(user_input.get(CONF_MODBUS_UNIT, 1)),
                            )
                    except Exception:  # noqa: BLE001 - reported as a form error
                        errors["base"] = "cannot_connect"
            if not errors:
                self._data.update(user_input)
                self._data[CONF_MODBUS_ENABLED] = modbus_enabled
                self._data[CONF_MODBUS_WRITE_ENABLED] = bool(
                    modbus_enabled and write_enabled
                )
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
            menu_options=["entities", "finance", "grid", "modbus"],
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
            if modbus_enabled:
                if not host or not HOST_RE.fullmatch(host):
                    errors["base"] = "invalid_host"
                else:
                    try:
                        async with asyncio.timeout(12):
                            await _test_modbus(
                                host,
                                int(user_input.get(CONF_MODBUS_PORT, 502)),
                                int(user_input.get(CONF_MODBUS_UNIT, 1)),
                            )
                    except Exception:  # noqa: BLE001
                        errors["base"] = "cannot_connect"
            if not errors:
                user_input[CONF_MODBUS_ENABLED] = modbus_enabled
                user_input[CONF_MODBUS_WRITE_ENABLED] = bool(
                    modbus_enabled and write_enabled
                )
                user_input[CONF_MODBUS_HOST] = host
                return self._finish(user_input)
        return self.async_show_form(
            step_id="modbus",
            data_schema=_modbus_schema({**self._merged(), **user_input} if user_input else self._merged()),
            errors=errors,
        )
