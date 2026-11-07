"""Binary sensors for HEMS Master."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HEMSDataUpdateCoordinator
from .entity import HEMSBaseEntity

ValueFn = Callable[[dict[str, Any]], bool | None]


def _path(data: Mapping[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _modbus_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return dict(_path(data, "modbus") or {})


def _ev_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return dict(_path(data, "ev") or {})


@dataclass(frozen=True, slots=True)
class HEMSBinarySensorSpec:
    key: str
    name: str
    path: tuple[str, ...]
    icon: str = "mdi:checkbox-blank-circle"
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


BINARY_SENSORS: tuple[HEMSBinarySensorSpec, ...] = (
    HEMSBinarySensorSpec(
        "modbus_connected",
        "Modbus Connected",
        ("modbus", "connected"),
        icon="mdi:lan-connect",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        attrs_fn=_modbus_attrs,
    ),
    HEMSBinarySensorSpec(
        "fallback_active",
        "Grid Fallback Active",
        ("modbus", "fallback"),
        icon="mdi:shield-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        attrs_fn=_modbus_attrs,
    ),
    HEMSBinarySensorSpec(
        "ev_connected",
        "EV Connected",
        ("ev", "connected"),
        icon="mdi:ev-plug-type2",
        device_class=BinarySensorDeviceClass.PLUG,
        attrs_fn=_ev_attrs,
    ),
    HEMSBinarySensorSpec(
        "ev_charging",
        "EV Charging",
        ("ev", "charging"),
        icon="mdi:car-electric",
        device_class=BinarySensorDeviceClass.RUNNING,
        attrs_fn=_ev_attrs,
    ),
    HEMSBinarySensorSpec(
        "ev_charging_paused",
        "EV Charging Paused",
        ("ev", "paused"),
        icon="mdi:pause-circle",
        attrs_fn=_ev_attrs,
    ),
)


class HEMSCoordinatorBinarySensor(HEMSBaseEntity, BinarySensorEntity):
    """Binary sensor backed by the coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HEMSDataUpdateCoordinator,
        spec: HEMSBinarySensorSpec,
    ) -> None:
        super().__init__(coordinator, f"binary_{spec.key}")
        self.spec = spec
        self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category

    @property
    def is_on(self) -> bool | None:
        value = _path(self.snapshot, *self.spec.path)
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.spec.attrs_fn is None:
            return {}
        try:
            return self.spec.attrs_fn(self.snapshot)
        except Exception:  # pragma: no cover
            return {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HEMSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HEMSCoordinatorBinarySensor(coordinator, spec) for spec in BINARY_SENSORS
    )
