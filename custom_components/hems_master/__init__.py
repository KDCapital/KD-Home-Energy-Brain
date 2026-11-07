"""HEMS Master integration setup."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError

from .const import (
    CONF_MODBUS_ENABLED,
    DOMAIN,
    PLATFORMS,
    SERVICE_APPLY_PLAN,
    SERVICE_EXPORT_AUDIT,
    SERVICE_RECALCULATE,
    SERVICE_RESET_TODAY,
)
from .coordinator import HEMSDataUpdateCoordinator
from .modbus_client import modbus_available

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration (configuration is entry-only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one HEMS Master config entry."""
    config = {**entry.data, **entry.options}
    if config.get(CONF_MODBUS_ENABLED) and not modbus_available():
        raise ConfigEntryError(
            "pymodbus is not installed; Modbus control is enabled but the dependency is missing"
        )
    hass.data.setdefault(DOMAIN, {})
    coordinator = HEMSDataUpdateCoordinator(hass, entry)
    await coordinator.async_initialize()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and stop timers/Modbus cleanly."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: HEMSDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_shutdown()
    if unload_ok and not hass.data.get(DOMAIN):
        _async_remove_services(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the user saves options."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_coordinator(hass: HomeAssistant) -> HEMSDataUpdateCoordinator:
    entries: dict[str, HEMSDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    for coordinator in entries.values():
        return coordinator
    raise HomeAssistantError("HEMS Master is not set up")


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RECALCULATE):
        return

    async def async_recalculate(call: ServiceCall) -> None:
        await _get_coordinator(hass).async_request_refresh()

    async def async_reset_today(call: ServiceCall) -> None:
        await _get_coordinator(hass).async_reset_today()

    async def async_apply_plan(call: ServiceCall) -> None:
        await _get_coordinator(hass).async_force_apply()

    async def async_export_audit(call: ServiceCall) -> dict[str, Any]:
        return _get_coordinator(hass).export_audit()

    hass.services.async_register(DOMAIN, SERVICE_RECALCULATE, async_recalculate)
    hass.services.async_register(DOMAIN, SERVICE_RESET_TODAY, async_reset_today)
    hass.services.async_register(DOMAIN, SERVICE_APPLY_PLAN, async_apply_plan)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_AUDIT,
        async_export_audit,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _async_remove_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_RECALCULATE,
        SERVICE_RESET_TODAY,
        SERVICE_APPLY_PLAN,
        SERVICE_EXPORT_AUDIT,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
