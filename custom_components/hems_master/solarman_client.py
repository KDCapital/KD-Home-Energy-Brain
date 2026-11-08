"""Deye access through the existing Home Assistant Solarman integration.

This is the preferred transport when a Solarman/IGEN-Tech logger already owns
the inverter connection (port 8899, transport ``tcp``, profile auto). The
Solarman integration handles the proprietary V5 framing, logger serial and
reconnects; HEMS Master only calls its register services.

Required Solarman services (current ha-solarman):
* ``solarman.read_holding_registers``
* ``solarman.write_single_register``
* ``solarman.write_multiple_registers``

The write transaction still implements the Deye register 1100 unlock/lock
protocol.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    MODBUS_LOCK_VALUE,
    MODBUS_UNLOCK_VALUE,
    REG_UNLOCK,
)
from .modbus_client import (
    InverterSettings,
    ModbusDependencyError,
    ModbusError,
    ModbusProtocolError,
)

_LOGGER = logging.getLogger(__name__)

SOLARMAN_DOMAIN = "solarman"
SERVICE_READ_HOLDING = "read_holding_registers"
SERVICE_WRITE_SINGLE = "write_single_register"
SERVICE_WRITE_MULTIPLE = "write_multiple_registers"
# Older releases used these names.
SERVICE_WRITE_SINGLE_LEGACY = "write_holding_register"
SERVICE_WRITE_MULTIPLE_LEGACY = "write_multiple_holding_registers"

# Solarman's service implementation uses ``address or register`` internally,
# so address 0 is treated as missing and fails. Register 3 is a harmless
# non-zero holding register used only as a connectivity probe.
PING_REGISTER = 3


def solarman_available(hass: HomeAssistant) -> bool:
    """Return whether the Solarman integration exposes its read service."""
    return hass.services.has_service(SOLARMAN_DOMAIN, SERVICE_READ_HOLDING)


def solarman_write_available(hass: HomeAssistant) -> bool:
    """Return whether Solarman exposes single and multiple write services."""
    single = hass.services.has_service(
        SOLARMAN_DOMAIN, SERVICE_WRITE_SINGLE
    ) or hass.services.has_service(SOLARMAN_DOMAIN, SERVICE_WRITE_SINGLE_LEGACY)
    multiple = hass.services.has_service(
        SOLARMAN_DOMAIN, SERVICE_WRITE_MULTIPLE
    ) or hass.services.has_service(
        SOLARMAN_DOMAIN, SERVICE_WRITE_MULTIPLE_LEGACY
    )
    return single and multiple


class DeyeSolarmanClient:
    """Deye client backed by the Solarman integration's service API."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        self.hass = hass
        self.device_id = device_id
        self._lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_success: datetime | None = None

    @property
    def connected(self) -> bool:
        return solarman_available(self.hass) and bool(self.device_id)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_success(self) -> datetime | None:
        return self._last_success

    def _write_service(self) -> str:
        if self.hass.services.has_service(SOLARMAN_DOMAIN, SERVICE_WRITE_SINGLE):
            return SERVICE_WRITE_SINGLE
        if self.hass.services.has_service(SOLARMAN_DOMAIN, SERVICE_WRITE_SINGLE_LEGACY):
            return SERVICE_WRITE_SINGLE_LEGACY
        raise ModbusDependencyError(
            "The Solarman integration does not expose a write-single-register service"
        )

    def _write_multiple_service(self) -> str:
        if self.hass.services.has_service(SOLARMAN_DOMAIN, SERVICE_WRITE_MULTIPLE):
            return SERVICE_WRITE_MULTIPLE
        if self.hass.services.has_service(
            SOLARMAN_DOMAIN, SERVICE_WRITE_MULTIPLE_LEGACY
        ):
            return SERVICE_WRITE_MULTIPLE_LEGACY
        raise ModbusDependencyError(
            "The Solarman integration does not expose a write-multiple-registers service"
        )

    async def _ensure_service(self) -> None:
        if not self.connected:
            raise ModbusDependencyError(
                "The Solarman integration or its read_holding_registers service is unavailable"
            )

    async def _read_holding(self, address: int) -> int:
        await self._ensure_service()
        try:
            response = await self.hass.services.async_call(
                SOLARMAN_DOMAIN,
                SERVICE_READ_HOLDING,
                {"device": self.device_id, "address": address, "count": 1},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - service call is user-configured
            self._last_error = f"Solarman read {address} failed: {err}"
            raise ModbusError(self._last_error) from err
        value = self._extract_response_value(response, address)
        if value is None:
            self._last_error = f"Solarman read {address} returned no value"
            raise ModbusProtocolError(self._last_error)
        self._last_success = dt_util.utcnow()
        self._last_error = None
        return value

    @staticmethod
    def _extract_response_value(response: Any, address: int) -> int | None:
        if isinstance(response, Mapping):
            for key in (address, str(address)):
                if key in response:
                    value = response[key]
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        return None
            if len(response) == 1:
                try:
                    return int(next(iter(response.values())))
                except (TypeError, ValueError):
                    return None
        if isinstance(response, (int, float)):
            return int(response)
        return None

    async def _write_single(self, address: int, value: int) -> None:
        await self._ensure_service()
        service = self._write_service()
        data: dict[str, Any] = {"device": self.device_id, "value": int(value)}
        data["register" if service == SERVICE_WRITE_SINGLE_LEGACY else "address"] = address
        try:
            await self.hass.services.async_call(
                SOLARMAN_DOMAIN,
                service,
                data,
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Solarman write {address} failed: {err}"
            raise ModbusError(self._last_error) from err
        self._last_success = dt_util.utcnow()
        self._last_error = None

    async def _write_multiple(self, address: int, values: list[int]) -> None:
        await self._ensure_service()
        service = self._write_multiple_service()
        data: dict[str, Any] = {
            "device": self.device_id,
            "values": [int(value) for value in values],
        }
        data["register" if service == SERVICE_WRITE_MULTIPLE_LEGACY else "address"] = address
        try:
            await self.hass.services.async_call(
                SOLARMAN_DOMAIN,
                service,
                data,
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            self._last_error = f"Solarman write registers at {address} failed: {err}"
            raise ModbusError(self._last_error) from err
        self._last_success = dt_util.utcnow()
        self._last_error = None

    async def async_ping(self) -> bool:
        async with self._lock:
            try:
                await self._read_holding(PING_REGISTER)
                return True
            except ModbusDependencyError:
                raise
            except ModbusError:
                return False

    async def async_test_connection(self) -> bool:
        async with self._lock:
            await self._read_holding(PING_REGISTER)
            return True

    async def async_read_register(self, address: int) -> int:
        async with self._lock:
            return await self._read_holding(address)

    async def async_apply_settings(
        self, settings: InverterSettings, *, verify: bool = True
    ) -> dict[str, Any]:
        """Apply settings with the Deye 1100 unlock/lock transaction."""
        writes = settings.as_registers()
        error: Exception | None = None
        unlock_succeeded = False
        verified: list[dict[str, int]] = []

        async with self._lock:
            try:
                await self._write_single(REG_UNLOCK, MODBUS_UNLOCK_VALUE)
                unlock_succeeded = True
                for address, value in writes:
                    await self._write_multiple(address, [value])
                    await asyncio.sleep(0.05)

                if verify:
                    for address, expected in writes:
                        actual = await self._read_holding(address)
                        verified.append(
                            {"address": address, "expected": expected, "actual": actual}
                        )
                        if actual != expected:
                            raise ModbusProtocolError(
                                f"Verification failed for register {address}: "
                                f"wrote {expected}, read {actual}"
                            )
            except Exception as err:  # noqa: BLE001
                error = err
            finally:
                if unlock_succeeded:
                    try:
                        await self._write_single(REG_UNLOCK, MODBUS_LOCK_VALUE)
                    except Exception as lock_err:  # noqa: BLE001
                        _LOGGER.error(
                            "Could not re-lock Deye register 1100 through Solarman: %s",
                            lock_err,
                        )
                        if error is None:
                            error = ModbusProtocolError(
                                "Settings written but register 1100 could not be locked: "
                                f"{lock_err}"
                            )
                        else:
                            _LOGGER.error(
                                "Original Solarman error before failed re-lock: %s", error
                            )

            if error is not None:
                self._last_error = str(error)
                if isinstance(error, ModbusError):
                    raise error
                raise ModbusError(str(error)) from error

        return {
            "transport": "solarman",
            "device": self.device_id,
            "settings": settings.as_dict(),
            "writes": [{"address": a, "value": v} for a, v in writes],
            "verified": verified,
            "unlocked": unlock_succeeded,
            "locked": True,
        }

    async def async_close(self) -> None:
        """The Solarman integration owns the connection; nothing to close."""
        self._last_error = None
