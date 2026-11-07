"""Adjustable HEMS Master parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EMPLOYER_RATE,
    CONF_ERE_RATE,
    CONF_MAX_GRID_CURRENT,
    CONF_MIN_ARBITRAGE_MARGIN,
    DOMAIN,
)
from .coordinator import HEMSDataUpdateCoordinator
from .entity import HEMSBaseEntity


@dataclass(frozen=True, slots=True)
class HEMSNumberSpec:
    key: str
    name: str
    icon: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None
    precision: int = 3
    entity_category: EntityCategory | None = EntityCategory.CONFIG


NUMBERS: tuple[HEMSNumberSpec, ...] = (
    HEMSNumberSpec(
        CONF_EMPLOYER_RATE,
        "Employer Rate",
        "mdi:briefcase-account",
        0.0,
        5.0,
        0.001,
        "EUR/kWh",
        3,
    ),
    HEMSNumberSpec(
        CONF_ERE_RATE,
        "ERE Rate",
        "mdi:leaf",
        0.0,
        2.0,
        0.001,
        "EUR/kWh",
        3,
    ),
    HEMSNumberSpec(
        CONF_MIN_ARBITRAGE_MARGIN,
        "Min Arbitrage Margin",
        "mdi:scale-balance",
        0.0,
        1.0,
        0.001,
        "EUR/kWh",
        3,
    ),
    HEMSNumberSpec(
        CONF_MAX_GRID_CURRENT,
        "Max Grid Current",
        "mdi:fuse",
        1.0,
        125.0,
        1.0,
        UnitOfCurrent.AMPERE,
        0,
        None,
    ),
)


class HEMSNumber(HEMSBaseEntity, NumberEntity):
    """A writable runtime parameter stored in the HEMS ledger."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: HEMSDataUpdateCoordinator,
        spec: HEMSNumberSpec,
    ) -> None:
        super().__init__(coordinator, f"number_{spec.key}")
        self.spec = spec
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_category = spec.entity_category

    @property
    def native_value(self) -> float:
        return float(self.coordinator.settings.get(self.spec.key, self.spec.minimum))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = (self.snapshot.get("plan") or {})
        return {
            "parameter": self.spec.key,
            "plan_reason": plan.get("reason"),
        }

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting(self.spec.key, value)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HEMSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HEMSNumber(coordinator, spec) for spec in NUMBERS)
