"""Persistent BLE transport for the programming skill (via ``bleak``).

:class:`BleTransport` keeps one GATT connection open, writes frames as 16-byte slices with the
``randomId``/``serialNo`` trailer, and reassembles the robot's notifications into frames.
Unlike :class:`~.ble.BleakGattWriter`, which writes once and disconnects, it supports the
request/response exchange of a :class:`~.session.CodeMaoSession`.

Whether notifications carry the same 4-byte trailer is inferred from the analysis report;
``trailer_size=0`` selects raw fragments.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Self

from bleak import BleakClient
from bleak.exc import BleakError

from alpha_mini_wifi_encoder.chunking import ChunkingStrategy, TrailerChunker
from alpha_mini_wifi_encoder.constants import (
    GATT_CHUNK_TRAILER_SIZE,
    HEARTBEAT_CHARACTERISTIC_UUID,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
)
from alpha_mini_wifi_encoder.errors import BleError
from alpha_mini_wifi_encoder.frames import FrameStreamDecoder
from alpha_mini_wifi_encoder.ids import SystemRandomId

if TYPE_CHECKING:
    from collections.abc import Callable

_INTER_CHUNK_DELAY_SECONDS = 0.03
"""float: Delay in seconds between consecutive GATT writes."""


class BleTransport:
    """:class:`~.session.FrameTransport` over a persistent GATT connection."""

    def __init__(  # noqa: PLR0913 - one parameter per tunable of the link
        self,
        address: str,
        *,
        chunker: ChunkingStrategy | None = None,
        connect_timeout: float = 15.0,
        trailer_size: int = GATT_CHUNK_TRAILER_SIZE,
        heartbeat_interval: float | None = None,
        heartbeat_payload: bytes = b"\x00",
        client_factory: Callable[..., Any] = BleakClient,
    ) -> None:
        """Create the transport; nothing is opened until :meth:`connect`.

        Args:
            address (str): Platform address of the robot.
            chunker (ChunkingStrategy | None): Slicing strategy; defaults to the app's scheme
                with random ids drawn from the system entropy source.
            connect_timeout (float): Seconds allowed for the connection.
            trailer_size (int): Bytes to strip from each notification before reassembly;
                ``0`` for fragments without trailer.
            heartbeat_interval (float | None): Seconds between writes to the heartbeat
                characteristic; ``None`` disables the heartbeat.
            heartbeat_payload (bytes): Value written to the heartbeat characteristic.
            client_factory (Callable[..., Any]): Creates a client from an address (injectable
                for tests).

        Returns:
            None: This method does not return a value.
        """
        self._address = address
        self._chunker = chunker or TrailerChunker(SystemRandomId())
        self._connect_timeout = connect_timeout
        self._trailer_size = trailer_size
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_payload = heartbeat_payload
        self._client_factory = client_factory
        self._client: Any = None
        self._decoder = FrameStreamDecoder()
        self._inbox: asyncio.Queue[bytes | BleError] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        """Connect and subscribe to the notify characteristic.

        Returns:
            None: This method does not return a value.

        Raises:
            BleError: If the connection or the subscription fails.
        """
        self._loop = asyncio.get_running_loop()
        client = self._client_factory(
            self._address,
            timeout=self._connect_timeout,
            disconnected_callback=self._on_disconnect,
        )
        try:
            await client.connect()
            self._client = client
            await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._on_notification)
        except (BleakError, TimeoutError, OSError) as error:
            await self.close()
            msg = f"BLE connection to {self._address} failed: {error}"
            raise BleError(msg) from error
        if self._heartbeat_interval is not None:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(self._heartbeat_interval)
            )

    async def close(self) -> None:
        """Stop the heartbeat and disconnect; safe to call repeatedly.

        Returns:
            None: This method does not return a value.
        """
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(BleakError, TimeoutError, OSError):
                await client.disconnect()

    async def send(self, frame: bytes) -> None:
        """Write ``frame`` to the write characteristic, one slice at a time.

        Args:
            frame (bytes): Complete ``MiniBleProto`` frame.

        Returns:
            None: This method does not return a value.

        Raises:
            BleError: If not connected or if a write fails.
        """
        if self._client is None:
            msg = "BLE transport is not connected"
            raise BleError(msg)
        try:
            for chunk in self._chunker.split(frame):
                await self._client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, chunk, response=None)
                await asyncio.sleep(_INTER_CHUNK_DELAY_SECONDS)
        except (BleakError, TimeoutError, OSError) as error:
            msg = f"BLE write to {self._address} failed: {error}"
            raise BleError(msg) from error

    async def receive(self) -> bytes:
        """Wait for the next reassembled frame.

        Returns:
            bytes: Content of the received frame.

        Raises:
            BleError: If the link was lost.
        """
        item = await self._inbox.get()
        if isinstance(item, BleError):
            self._inbox.put_nowait(item)
            raise item
        return item

    async def __aenter__(self) -> Self:
        """Connect when used as an async context manager.

        Returns:
            Self: The connected transport.
        """
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close when leaving the async context.

        Args:
            *_exc (object): Exception information, ignored.

        Returns:
            None: This method does not return a value.
        """
        await self.close()

    def _on_notification(self, _characteristic: object, data: bytearray) -> None:
        """Handle a notification, which bleak may deliver from another thread.

        Args:
            _characteristic (object): Characteristic that notified; unused.
            data (bytearray): Notification value.

        Returns:
            None: This method does not return a value.
        """
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._push, bytes(data))

    def _push(self, data: bytes) -> None:
        """Reassemble ``data`` and queue every frame it completes.

        Args:
            data (bytes): Notification value, including its trailer if any.

        Returns:
            None: This method does not return a value.
        """
        payload = data[: len(data) - self._trailer_size] if self._trailer_size else data
        for content in self._decoder.feed(payload):
            self._inbox.put_nowait(content)

    def _on_disconnect(self, _client: object) -> None:
        """Queue a failure so that pending and future receives raise.

        Args:
            _client (object): Client that disconnected; unused.

        Returns:
            None: This method does not return a value.
        """
        if self._loop is not None:
            msg = f"BLE link to {self._address} lost"
            self._loop.call_soon_threadsafe(self._inbox.put_nowait, BleError(msg))

    async def _heartbeat_loop(self, interval: float) -> None:
        """Write the heartbeat value periodically until cancelled or the link fails.

        Args:
            interval (float): Seconds between writes.

        Returns:
            None: This method does not return a value.
        """
        while self._client is not None:
            await asyncio.sleep(interval)
            if self._client is None:
                return
            try:
                await self._client.write_gatt_char(
                    HEARTBEAT_CHARACTERISTIC_UUID,
                    self._heartbeat_payload,
                    response=None,
                )
            except (BleakError, TimeoutError, OSError):
                return
