"""Manual override switches for HEMS Master."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MODE_AUTO_ARBITRAGE,
    MODE_AUTO_EV_CHARGING,
    MODE_FORCE_EV_CHARGING,
    MODE_FORCE_GRID_CHARGE,
)
from .coordinator import HEMSDataUpdateCoordinator
from .entity import HEMSBaseEntity


@dataclass(frozen=True, slots=True)
class HEMSSwitchSpec:
    key: str
    mode: str
    name: str
    icon: str


SWITCHES: tuple[HEMSSwitchSpec, ...] = (
    HEMSSwitchSpec(
        "auto_arbitrage",
        MODE_AUTO_ARBITRAGE,
        "Auto Arbitrage",
        "mdi:robot",
    ),
    HEMSSwitchSpec(
        "auto_ev_charging",
        MODE_AUTO_EV_CHARGING,
        "Auto EV Charging",
        "mdi:car-clock",
    ),
    HEMSSwitchSpec(
        "force_ev_charging",
        MODE_FORCE_EV_CHARGING,
        "Force EV Charging",
        "mdi:car-electric",
    ),
    HEMSSwitchSpec(
        "force_grid_charge",
        MODE_FORCE_GRID_CHARGE,
        "Force Grid Charge",
        "mdi:battery-charging-100",
    ),
)


class HEMSModeSwitch(HEMSBaseEntity, SwitchEntity):
    """A switch mapped to one coordinator mode."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HEMSDataUpdateCoordinator,
        spec: HEMSSwitchSpec,
    ) -> None:
        super().__init__(coordinator, f"switch_{spec.key}")
        self.spec = spec
        self._attr_name = spec.name
        self._attr_icon = spec.icon

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.modes.get(self.spec.mode, False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.snapshot
        plan = data.get("plan") or {}
        return {
            "mode": self.spec.mode,
            "plan_reason": plan.get("reason"),
            "plan_applied": plan.get("applied"),
            "modbus_write_enabled": (data.get("modbus") or {}).get("write_enabled"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mode(self.spec.mode, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mode(self.spec.mode, False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HEMSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HEMSModeSwitch(coordinator, spec) for spec in SWITCHES)
