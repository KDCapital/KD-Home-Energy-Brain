"""Diagnostics support for HEMS Master."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MODBUS_HOST, DOMAIN

TO_REDACT = {CONF_MODBUS_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without exposing the Modbus host."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"error": "coordinator not loaded"}
    snapshot = dict(coordinator.data or {})
    modbus = dict(snapshot.get("modbus") or {})
    if modbus.get("last_error"):
        modbus["last_error"] = "Modbus connection or write error (details redacted)"
    snapshot["modbus"] = modbus
    return {
        "entry": async_redact_data(
            {**entry.data, **entry.options},
            TO_REDACT,
        ),
        "settings": dict(coordinator.settings),
        "modes": dict(coordinator.modes),
        "snapshot": snapshot,
        "audit": coordinator.export_audit(),
    }
