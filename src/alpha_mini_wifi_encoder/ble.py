"""Bluetooth Low Energy I/O: device discovery and GATT writes (via ``bleak``)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from alpha_mini_wifi_encoder.constants import (
    NOTIFY_CHARACTERISTIC_UUID,
    SERVICE_UUID,
    WRITE_CHARACTERISTIC_UUID,
)
from alpha_mini_wifi_encoder.errors import BleError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_INTER_CHUNK_DELAY_SECONDS = 0.03


@dataclass(frozen=True, slots=True)
class BleDevice:
    """A Bluetooth Low Energy device seen during a scan.

    Attributes:
        address: Platform address (MAC on Linux/Windows, UUID on macOS).
        name: Advertised local name (empty if none).
        rssi: Signal strength in dBm, if known.
        advertises_service: Whether the Alpha Mini service UUID was advertised.
    """

    address: str
    name: str
    rssi: int | None = None
    advertises_service: bool = False

    @property
    def label(self) -> str:
        """Menu label: name, address, signal and service marker."""
        signal = f"{self.rssi} dBm" if self.rssi is not None else "? dBm"
        star = "  [Alpha Mini service]" if self.advertises_service else ""
        return f"{self.name or '(unnamed)'}  {self.address}  {signal}{star}"


class DeviceScanner(Protocol):
    """Strategy that discovers nearby BLE devices."""

    async def scan(self, timeout: float) -> list[BleDevice]:  # noqa: ASYNC109 - plain duration
        """Scan for ``timeout`` seconds and return the devices found."""
        ...


class GattWriter(Protocol):
    """Strategy that delivers GATT write values to a device."""

    async def send(
        self,
        address: str,
        chunks: Sequence[bytes],
        *,
        listen_seconds: float = 0.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[bytes, ...]:
        """Write ``chunks`` in order and return notifications received afterwards."""
        ...


class BleakDeviceScanner:
    """:class:`DeviceScanner` backed by :class:`bleak.BleakScanner`."""

    def __init__(self, *, include_unnamed: bool = False) -> None:
        """Create the scanner.

        Args:
            include_unnamed: Keep devices that advertise no name.
        """
        self._include_unnamed = include_unnamed

    async def scan(self, timeout: float) -> list[BleDevice]:  # noqa: ASYNC109 - plain duration
        """Scan and return devices sorted by (service advertised, signal strength).

        Raises:
            BleError: If the Bluetooth adapter is unavailable or the scan fails.
        """
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        except (BleakError, OSError) as error:
            msg = f"Bluetooth scan failed: {error}"
            raise BleError(msg) from error
        devices = [
            BleDevice(
                address=device.address,
                name=adv.local_name or device.name or "",
                rssi=adv.rssi,
                advertises_service=SERVICE_UUID in {u.lower() for u in adv.service_uuids},
            )
            for device, adv in found.values()
        ]
        if not self._include_unnamed:
            devices = [d for d in devices if d.name]
        return sorted(devices, key=lambda d: (not d.advertises_service, -(d.rssi or -999)))


@dataclass
class BleakGattWriter:
    """:class:`GattWriter` backed by :class:`bleak.BleakClient`.

    Attributes:
        connect_timeout: Seconds allowed for the connection.
        client_factory: Callable creating a client from an address (injectable).
    """

    connect_timeout: float = 15.0
    client_factory: Callable[..., Any] = field(default=BleakClient)

    async def send(
        self,
        address: str,
        chunks: Sequence[bytes],
        *,
        listen_seconds: float = 0.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[bytes, ...]:
        """Connect, write every chunk to the write characteristic, then listen.

        Args:
            address: Device address from a scan.
            chunks: GATT write values, in order.
            listen_seconds: How long to collect notifications after writing.
            on_progress: Called with ``(written, total)`` after each write.

        Returns:
            Notifications received on the notify characteristic.

        Raises:
            BleError: On connection, timeout or write failure.
        """
        notifications: list[bytes] = []
        try:
            async with self.client_factory(address, timeout=self.connect_timeout) as client:
                if listen_seconds > 0:
                    await self._subscribe(client, notifications)
                for index, chunk in enumerate(chunks, 1):
                    await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, chunk, response=None)
                    if on_progress is not None:
                        on_progress(index, len(chunks))
                    await asyncio.sleep(_INTER_CHUNK_DELAY_SECONDS)
                if listen_seconds > 0:
                    await asyncio.sleep(listen_seconds)
        except (BleakError, TimeoutError, OSError) as error:
            msg = f"BLE write to {address} failed: {error}"
            raise BleError(msg) from error
        return tuple(notifications)

    @staticmethod
    async def _subscribe(client: Any, sink: list[bytes]) -> None:  # noqa: ANN401
        """Subscribe to notifications; a missing characteristic is not fatal."""
        try:
            await client.start_notify(
                NOTIFY_CHARACTERISTIC_UUID,
                lambda _char, data: sink.append(bytes(data)),
            )
        except BleakError:
            return
