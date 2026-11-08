"""Best-effort entity auto-detection for common HEMS source integrations.

The user still confirms the mappings in the normal entity step. Detection is
deliberately conservative: a source only fills a target when a plausible entity
is found, and manual choices are not made mandatory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BARE_PRICE_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_EV_CONNECTED_ENTITY,
    CONF_EV_CURRENT_ENTITY,
    CONF_EV_CURRENT_NUMBER_ENTITY,
    CONF_EV_MODE_SELECT_ENTITY,
    CONF_EV_POWER_ENTITY,
    CONF_GRID_CURRENT_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PV_POWER_ENTITY,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    entity_id: str
    domain: str
    name: str
    device_class: str
    text: str


def _candidates(hass: HomeAssistant, device_id: str | None) -> list[_Candidate]:
    if not device_id:
        return []
    registry = er.async_get(hass)
    result: list[_Candidate] = []
    for entry in er.async_entries_for_device(registry, device_id):
        if getattr(entry, "disabled_by", None) is not None:
            continue
        if entry.platform == "hems_master":
            continue
        name = str(entry.name or entry.original_name or entry.entity_id)
        domain = entry.entity_id.split(".", 1)[0]
        device_class = str(
            getattr(entry, "device_class", None)
            or getattr(entry, "original_device_class", None)
            or ""
        )
        text = f"{entry.entity_id} {name} {device_class}".lower().replace("_", " ")
        result.append(
            _Candidate(
                entity_id=entry.entity_id,
                domain=domain,
                name=name,
                device_class=device_class.lower(),
                text=text,
            )
        )
    return result


def _pick(
    candidates: list[_Candidate],
    *,
    domain: str,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> str | None:
    best: tuple[int, str] | None = None
    for candidate in candidates:
        if candidate.domain != domain:
            continue
        if any(token in candidate.text for token in exclude):
            continue
        matches = sum(1 for token in include if token in candidate.text)
        if include and matches == 0:
            continue
        score = matches * 10 - len(candidate.entity_id) / 1000
        if best is None or score > best[0]:
            best = (score, candidate.entity_id)
    return best[1] if best else None


def _by_suffix(
    candidates: list[_Candidate], *, domain: str, suffix: str
) -> str | None:
    for candidate in candidates:
        if candidate.domain == domain and candidate.entity_id.endswith(suffix):
            return candidate.entity_id
    return None


def _detect_solarman(candidates: list[_Candidate]) -> dict[str, str]:
    detected: dict[str, str] = {}
    pv = _pick(
        candidates,
        domain="sensor",
        include=("power",),
        exclude=("current", "today", "energy", "load", "grid", "battery"),
    )
    if pv is None:
        pv = _pick(
            candidates,
            domain="sensor",
            include=("pv", "power"),
            exclude=("current", "today", "energy"),
        )
    if pv:
        detected[CONF_PV_POWER_ENTITY] = pv

    battery_power = _pick(
        candidates,
        domain="sensor",
        include=("battery", "power"),
        exclude=("soc", "charge", "capacity"),
    )
    if battery_power:
        detected[CONF_BATTERY_POWER_ENTITY] = battery_power

    battery_soc = _pick(
        candidates,
        domain="sensor",
        include=("battery", "soc"),
    )
    if battery_soc is None:
        battery_soc = _by_suffix(
            candidates, domain="sensor", suffix="battery_state_of_charge"
        )
    if battery_soc:
        detected[CONF_BATTERY_SOC_ENTITY] = battery_soc

    grid_power = _pick(
        candidates,
        domain="sensor",
        include=("grid", "power"),
        exclude=("current", "today", "energy", "ct"),
    )
    if grid_power:
        detected[CONF_GRID_POWER_ENTITY] = grid_power

    grid_current = _pick(
        candidates,
        domain="sensor",
        include=("grid", "current"),
        exclude=("power",),
    )
    if grid_current:
        detected[CONF_GRID_CURRENT_ENTITY] = grid_current

    house_load = _pick(
        candidates,
        domain="sensor",
        include=("load", "power"),
        exclude=("current", "today", "energy"),
    )
    if house_load is None:
        house_load = _pick(
            candidates,
            domain="sensor",
            include=("consumption", "power"),
            exclude=("today", "energy"),
        )
    if house_load:
        detected[CONF_HOUSE_LOAD_ENTITY] = house_load
    return detected


def _detect_frank(candidates: list[_Candidate]) -> dict[str, str]:
    detected: dict[str, str] = {}
    full = _by_suffix(
        candidates,
        domain="sensor",
        suffix="current_electricity_price_all_in",
    ) or _pick(
        candidates,
        domain="sensor",
        include=("current", "electricity", "price", "all", "in"),
        exclude=("gas", "feed"),
    )
    if full:
        detected[CONF_PRICE_ENTITY] = full
    bare = _by_suffix(
        candidates,
        domain="sensor",
        suffix="current_electricity_market_price",
    ) or _pick(
        candidates,
        domain="sensor",
        include=("current", "electricity", "market", "price"),
        exclude=("gas", "feed"),
    )
    if bare:
        detected[CONF_BARE_PRICE_ENTITY] = bare
    return detected


def _detect_homewizard(candidates: list[_Candidate]) -> dict[str, str]:
    detected: dict[str, str] = {}
    power = _pick(
        candidates,
        domain="sensor",
        include=("active", "power"),
        exclude=("l1", "l2", "l3", "phase", "production", "battery", "group"),
    )
    if power:
        detected[CONF_GRID_POWER_ENTITY] = power
    current = _pick(
        candidates,
        domain="sensor",
        include=("active", "current"),
        exclude=("l1", "l2", "l3", "phase"),
    )
    if current:
        detected[CONF_GRID_CURRENT_ENTITY] = current
    return detected


def _detect_evcc(candidates: list[_Candidate]) -> dict[str, str]:
    detected: dict[str, str] = {}
    current_setpoint = _pick(
        candidates,
        domain="select",
        include=("current",),
        exclude=("charge current",),
    )
    if current_setpoint is None:
        current_setpoint = _pick(
            candidates,
            domain="number",
            include=("max", "current"),
        )
    if current_setpoint:
        detected[CONF_EV_CURRENT_NUMBER_ENTITY] = current_setpoint

    mode = _pick(
        candidates,
        domain="select",
        include=("mode",),
        exclude=("current",),
    )
    if mode:
        detected[CONF_EV_MODE_SELECT_ENTITY] = mode

    power = _pick(
        candidates,
        domain="sensor",
        include=("charge", "power"),
        exclude=("total", "session"),
    )
    if power:
        detected[CONF_EV_POWER_ENTITY] = power

    current = _pick(
        candidates,
        domain="sensor",
        include=("charge", "current"),
        exclude=("max", "limit", "setpoint"),
    )
    if current:
        detected[CONF_EV_CURRENT_ENTITY] = current

    connected = _pick(
        candidates,
        domain="binary_sensor",
        include=("connected",),
    )
    if connected is None:
        connected = _pick(
            candidates,
            domain="binary_sensor",
            include=("plug",),
            exclude=("charging",),
        )
    if connected:
        detected[CONF_EV_CONNECTED_ENTITY] = connected
    return detected


def auto_detect_sources(
    hass: HomeAssistant,
    source_devices: dict[str, str | None],
) -> dict[str, str]:
    """Return detected entity mappings for the selected source devices."""
    detected: dict[str, str] = {}
    solarman = _candidates(hass, source_devices.get("solarman"))
    if solarman:
        detected.update(_detect_solarman(solarman))
    frank = _candidates(hass, source_devices.get("frank_energie"))
    if frank:
        detected.update(_detect_frank(frank))
    homewizard = _candidates(hass, source_devices.get("homewizard"))
    if homewizard:
        detected.update(_detect_homewizard(homewizard))
    evcc = _candidates(hass, source_devices.get("evcc"))
    if evcc:
        detected.update(_detect_evcc(evcc))
    return detected
