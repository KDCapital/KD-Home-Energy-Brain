"""Shared entity base for HEMS Master."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_NAME, DOMAIN, VERSION
from .coordinator import HEMSDataUpdateCoordinator


class HEMSBaseEntity(CoordinatorEntity[HEMSDataUpdateCoordinator]):
    """Base class that groups all entities under one HEMS device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: HEMSDataUpdateCoordinator,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer="HEMS Master",
            model="Home Energy Management System",
            entry_type=DeviceEntryType.SERVICE,
            sw_version=VERSION,
        )

    @property
    def snapshot(self) -> dict:
        return self.coordinator.data or {}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
