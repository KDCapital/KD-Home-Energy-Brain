"""Safe Modbus TCP access to a Deye hybrid inverter.

The Deye requires a write-unlock/write-lock sequence around configuration
changes. This client makes that sequence explicit and always tries to re-lock
register 1100, even when a settings write fails. It never leaves the inverter
unlocked silently.

Only the registers from the project specification are written:
130, 145, 166, 172 and 1100. Reading register 0 is used as a lightweight
connectivity probe.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    MODBUS_LOCK_VALUE,
    MODBUS_TIMEOUT,
    MODBUS_UNLOCK_VALUE,
    REG_CHARGE_ENABLE_SLOT_1,
    REG_GRID_CHARGE,
    REG_SOLAR_SELL_SLOT_1,
    REG_TARGET_SOC_SLOT_1,
    REG_UNLOCK,
)

_LOGGER = logging.getLogger(__name__)

try:  # Import lazily so the config flow can report a clean dependency error.
    from pymodbus.client import AsyncModbusTcpClient
except ImportError:  # pragma: no cover - exercised only without requirements
    AsyncModbusTcpClient = None  # type: ignore[assignment,misc]


def modbus_available() -> bool:
    """Return whether the pymodbus requirement is importable."""
    return AsyncModbusTcpClient is not None


class ModbusError(Exception):
    """Base exception for HEMS Modbus failures."""


class ModbusDependencyError(ModbusError):
    """Raised when pymodbus is not installed."""


class ModbusConnectionError(ModbusError):
    """Raised when the inverter cannot be reached."""


class ModbusProtocolError(ModbusError):
    """Raised for an exception response or malformed response."""


@dataclass(frozen=True, slots=True)
class InverterSettings:
    """The subset of Deye settings controlled by HEMS Master."""

    grid_charge: bool
    target_soc: int
    solar_sell: bool
    slot_1_charge: bool
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_soc", max(0, min(100, int(self.target_soc))))

    def as_registers(self) -> list[tuple[int, int]]:
        """Return (address, value) pairs in the documented write order."""
        return [
            (REG_GRID_CHARGE, int(self.grid_charge)),
            (REG_CHARGE_ENABLE_SLOT_1, int(self.slot_1_charge)),
            (REG_SOLAR_SELL_SLOT_1, int(self.solar_sell)),
            (REG_TARGET_SOC_SLOT_1, int(self.target_soc)),
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "grid_charge": self.grid_charge,
            "target_soc": self.target_soc,
            "solar_sell": self.solar_sell,
            "slot_1_charge": self.slot_1_charge,
            "reason": self.reason,
        }

    def key(self) -> tuple[int, int, int, int]:
        return (
            int(self.grid_charge),
            int(self.target_soc),
            int(self.solar_sell),
            int(self.slot_1_charge),
        )


class DeyeModbusClient:
    """Async, serialized Modbus TCP client for one Deye inverter."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        slave_id: int = 1,
        timeout: float = MODBUS_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.slave_id = int(slave_id)
        self.timeout = float(timeout)
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_success: datetime | None = None

    @property
    def connected(self) -> bool:
        client = self._client
        return bool(client is not None and getattr(client, "connected", False))

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_success(self) -> datetime | None:
        return self._last_success

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_dependency() -> None:
        if AsyncModbusTcpClient is None:
            raise ModbusDependencyError(
                "pymodbus is not installed; the integration requirement could not be loaded"
            )

    def _unit_kwargs(self, method: Any) -> dict[str, int]:
        """Return the unit/slave keyword accepted by this pymodbus version."""
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        for name in ("device_id", "slave", "unit"):
            if name in parameters:
                return {name: self.slave_id}
        if any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return {"slave": self.slave_id}
        return {}

    async def _close_unlocked(self) -> None:
        if self._client is None:
            return
        try:
            result = self._client.close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # pragma: no cover - best-effort close
            _LOGGER.debug("Error while closing Modbus client", exc_info=True)
        finally:
            self._client = None

    async def async_close(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def _ensure_connected(self) -> None:
        self._ensure_dependency()
        if self.connected:
            return
        await self._close_unlocked()
        client = AsyncModbusTcpClient(  # type: ignore[misc]
            host=self.host,
            port=self.port,
            timeout=self.timeout,
        )
        try:
            connected = await client.connect()
        except Exception as err:
            try:
                result = client.close()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover
                pass
            self._last_error = f"Cannot connect to {self.host}:{self.port}: {err}"
            raise ModbusConnectionError(self._last_error) from err
        if connected is False or not getattr(client, "connected", False):
            try:
                result = client.close()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover
                pass
            self._last_error = f"Cannot connect to {self.host}:{self.port}"
            raise ModbusConnectionError(self._last_error)
        self._client = client
        self._last_error = None

    # ------------------------------------------------------------------
    # Low-level operations
    # ------------------------------------------------------------------
    @staticmethod
    def _raise_for_error(response: Any, operation: str) -> None:
        if response is None:
            raise ModbusProtocolError(f"{operation}: empty response")
        is_error = getattr(response, "isError", None)
        if callable(is_error) and is_error():
            code = getattr(response, "exception_code", None)
            if code is None:
                code = getattr(response, "exceptionCode", None)
            if isinstance(code, int):
                function_code = getattr(response, "function_code", 0)
                function_code = function_code if isinstance(function_code, int) else 0
                raise ModbusProtocolError(
                    f"{operation}: Modbus exception 0x{code:02X} "
                    f"(function 0x{function_code:02X})"
                )
            raise ModbusProtocolError(f"{operation}: Modbus exception response")

    async def _read_holding_unlocked(self, address: int, count: int = 1) -> list[int]:
        await self._ensure_connected()
        client = self._client
        if client is None:  # pragma: no cover - defensive
            raise ModbusConnectionError("No Modbus client")
        try:
            response = await client.read_holding_registers(
                address,
                count=count,
                **self._unit_kwargs(client.read_holding_registers),
            )
        except Exception as err:
            await self._close_unlocked()
            self._last_error = f"Read address {address} failed: {err}"
            raise ModbusConnectionError(self._last_error) from err
        try:
            self._raise_for_error(response, f"read {address}")
        except ModbusProtocolError as err:
            self._last_error = str(err)
            raise
        registers = getattr(response, "registers", None)
        if not registers:
            self._last_error = f"Read address {address} returned no registers"
            raise ModbusProtocolError(self._last_error)
        self._last_success = dt_util.utcnow()
        self._last_error = None
        return list(registers)

    async def _write_register_unlocked(self, address: int, value: int) -> None:
        await self._ensure_connected()
        client = self._client
        if client is None:  # pragma: no cover - defensive
            raise ModbusConnectionError("No Modbus client")
        try:
            response = await client.write_register(
                address,
                int(value),
                **self._unit_kwargs(client.write_register),
            )
        except Exception as err:
            await self._close_unlocked()
            self._last_error = f"Write address {address} failed: {err}"
            raise ModbusConnectionError(self._last_error) from err
        try:
            self._raise_for_error(response, f"write {address}")
        except ModbusProtocolError as err:
            self._last_error = str(err)
            raise
        self._last_success = dt_util.utcnow()
        self._last_error = None

    async def _write_registers_unlocked(self, address: int, values: list[int]) -> None:
        """Write one or more holding registers with function code 0x10."""
        await self._ensure_connected()
        client = self._client
        if client is None:  # pragma: no cover - defensive
            raise ModbusConnectionError("No Modbus client")
        try:
            response = await client.write_registers(
                address,
                [int(value) for value in values],
                **self._unit_kwargs(client.write_registers),
            )
        except Exception as err:
            await self._close_unlocked()
            self._last_error = f"Write registers at {address} failed: {err}"
            raise ModbusConnectionError(self._last_error) from err
        try:
            self._raise_for_error(response, f"write registers at {address}")
        except ModbusProtocolError as err:
            self._last_error = str(err)
            raise
        self._last_success = dt_util.utcnow()
        self._last_error = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def async_ping(self) -> bool:
        """Check connectivity by reading a known status register."""
        async with self._lock:
            try:
                await self._read_holding_unlocked(0, 1)
                return True
            except ModbusDependencyError:
                raise
            except ModbusError:
                return False

    async def async_test_connection(self) -> bool:
        """Config-flow connection test, independent of a running coordinator."""
        self._ensure_dependency()
        async with self._lock:
            try:
                await self._ensure_connected()
                await self._read_holding_unlocked(0, 1)
                return True
            finally:
                await self._close_unlocked()

    async def async_read_register(self, address: int) -> int:
        async with self._lock:
            values = await self._read_holding_unlocked(address, 1)
            return values[0]

    async def async_apply_settings(
        self,
        settings: InverterSettings,
        *,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Apply settings using the mandatory Deye unlock/lock transaction.

        The function is serialized with all other client operations. Register
        1100 is unlocked exactly once and attempts to lock exactly once. If a
        write fails, the lock is still attempted; if the lock itself fails the
        socket is closed to force a clean reconnect on the next attempt.
        """
        writes = settings.as_registers()
        error: Exception | None = None
        unlock_attempted = False
        verified: list[dict[str, int]] = []

        async with self._lock:
            try:
                await self._ensure_connected()
                unlock_attempted = True
                await self._write_register_unlocked(REG_UNLOCK, MODBUS_UNLOCK_VALUE)
                for address, value in writes:
                    await self._write_registers_unlocked(address, [value])
                    # The Deye documentation and field reports recommend a
                    # short gap between consecutive setting writes.
                    await asyncio.sleep(0.05)

                if verify:
                    for address, expected in writes:
                        actual = (await self._read_holding_unlocked(address, 1))[0]
                        verified.append(
                            {"address": address, "expected": expected, "actual": actual}
                        )
                        if actual != expected:
                            raise ModbusProtocolError(
                                f"Verification failed for register {address}: "
                                f"wrote {expected}, read {actual}"
                            )
            except Exception as err:
                error = err
            finally:
                if unlock_attempted:
                    try:
                        await self._write_register_unlocked(REG_UNLOCK, MODBUS_LOCK_VALUE)
                    except Exception as lock_err:
                        _LOGGER.error(
                            "Could not re-lock Deye register 1100 after a write: %s",
                            lock_err,
                        )
                        await self._close_unlocked()
                        if error is None:
                            error = ModbusProtocolError(
                                f"Settings written but register 1100 could not be locked: {lock_err}"
                            )
                        else:
                            _LOGGER.error(
                                "Original Modbus error before failed re-lock: %s", error
                            )

            if error is not None:
                self._last_error = str(error)
                if isinstance(error, ModbusError):
                    raise error
                raise ModbusError(str(error)) from error

        return {
            "host": self.host,
            "port": self.port,
            "slave": self.slave_id,
            "settings": settings.as_dict(),
            "writes": [{"address": a, "value": v} for a, v in writes],
            "verified": verified,
            "unlocked": unlock_attempted,
            "locked": True,
        }
