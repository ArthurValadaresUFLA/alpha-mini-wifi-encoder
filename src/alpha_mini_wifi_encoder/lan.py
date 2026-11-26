"""LAN transport for the programming skill: raw TCP carrying ``MiniBleProto`` frames.

The companion app runs this stack on Netty; here it is a small :mod:`asyncio` client. After the
TCP connection is open, :class:`LanTransport` performs the application handshake (``commandId``
0 answered by ``commandId`` 1000 within 15 s), then keeps the link alive: a heartbeat (``0x01``,
the pong to the robot's ``0x00`` ping) is written when the writer has been idle for 5 s, and the
link is dropped after 7 s without any read. Frames carry a checksum byte (``LanFrameLayer``).

Everything here is derived from static analysis: the handshake field numbers, the heartbeat
payload and the read-timeout behaviour are assumptions that were not validated on hardware.
There is no TLS or encryption on this channel, as in the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, CodeMaoRequest, parse_result
from alpha_mini_wifi_encoder.codemao_messages import LanHandshakeRequest, LanHandshakeResponse
from alpha_mini_wifi_encoder.constants import (
    APP_VERSION_NAME,
    LAN_HANDSHAKE_REQUEST_COMMAND,
    LAN_HANDSHAKE_RESPONSE_COMMAND,
    LAN_HANDSHAKE_TIMEOUT_SECONDS,
    LAN_HEARTBEAT_OUTBOUND_PONG,
    LAN_HEARTBEAT_PAYLOADS,
    LAN_READ_TIMEOUT_SECONDS,
    LAN_WRITER_IDLE_SECONDS,
)
from alpha_mini_wifi_encoder.errors import LanError, ProtocolError
from alpha_mini_wifi_encoder.frames import FrameStreamDecoder
from alpha_mini_wifi_encoder.layers import LanFrameLayer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_READ_SIZE = 4096
"""int: Maximum bytes requested from the socket per read."""

_HANDSHAKE_SERIAL = 0
"""int: ``sendSerial`` of the handshake, which precedes the session's own counter."""

type ConnectionFactory = Callable[
    [str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]
"""Alias of a callable opening a TCP connection from a host and a port."""


@dataclass(frozen=True, slots=True)
class LanIdentity:
    """Client identity sent in the LAN handshake.

    The app derives it from the device; this client generates fresh random identifiers
    instead of copying any real device's values.

    Attributes:
        uuid (str): Client identifier; stable per installation in the app.
        app_version (str): Version string of the companion app being mimicked.
        locale (str): Locale tag, e.g. ``"en"``.
        device_type (str): Client platform label (assumed ``"android"``).
        session_id (str): Identifier of this connection attempt.
    """

    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    app_version: str = APP_VERSION_NAME
    locale: str = "en"
    device_type: str = "android"
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class LanTransport:
    """:class:`~.session.FrameTransport` over a raw TCP connection to the robot."""

    def __init__(  # noqa: PLR0913 - one parameter per tunable of the link
        self,
        host: str,
        port: int,
        *,
        identity: LanIdentity | None = None,
        connect_timeout: float = 5.0,
        handshake_timeout: float = LAN_HANDSHAKE_TIMEOUT_SECONDS,
        read_timeout: float = LAN_READ_TIMEOUT_SECONDS,
        writer_idle: float = LAN_WRITER_IDLE_SECONDS,
        connection_factory: ConnectionFactory = asyncio.open_connection,
    ) -> None:
        """Create the transport; nothing is opened until :meth:`connect`.

        Args:
            host (str): IP address or host name of the robot (from mDNS).
            port (int): TCP port of the robot's control service (from mDNS).
            identity (LanIdentity | None): Handshake identity; random by default.
            connect_timeout (float): Seconds allowed to open the TCP connection.
            handshake_timeout (float): Seconds allowed for the handshake response.
            read_timeout (float): Seconds of read silence after which the link is dropped.
            writer_idle (float): Seconds of write silence after which a heartbeat is sent.
            connection_factory (ConnectionFactory): Opens the connection (injectable for tests).

        Returns:
            None: This method does not return a value.
        """
        self._host = host
        self._port = port
        self._identity = identity or LanIdentity()
        self._connect_timeout = connect_timeout
        self._handshake_timeout = handshake_timeout
        self._read_timeout = read_timeout
        self._writer_idle = writer_idle
        self._connection_factory = connection_factory
        self._codec = CodeMaoCodec.for_lan()
        self._decoder = FrameStreamDecoder()
        self._inbox: asyncio.Queue[bytes | LanError] = asyncio.Queue()
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._last_write = 0.0

    @property
    def codec(self) -> CodeMaoCodec:
        """Codec for LAN frames (checksum byte before ``ED``).

        Returns:
            CodeMaoCodec: The LAN codec.
        """
        return self._codec

    @property
    def handshake_result(self) -> LanHandshakeResult | None:
        """Return the robot's handshake answer once connected.

        Returns:
            LanHandshakeResult | None: The decoded response, or ``None`` before :meth:`connect`.
        """
        return self._handshake_result

    async def connect(self) -> None:
        """Open the TCP connection, run the application handshake and start the heartbeat.

        Returns:
            None: This method does not return a value.

        Raises:
            LanError: If the connection fails or the robot refuses or ignores the handshake.
        """
        try:
            async with asyncio.timeout(self._connect_timeout):
                reader, self._writer = await self._connection_factory(self._host, self._port)
        except (OSError, TimeoutError) as error:
            msg = f"cannot connect to {self._host}:{self._port}: {error}"
            raise LanError(msg) from error
        self._tasks.append(asyncio.create_task(self._read_loop(reader)))
        try:
            await self._handshake()
        except LanError:
            await self.close()
            raise
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

    async def close(self) -> None:
        """Stop the background tasks and close the socket; safe to call repeatedly.

        Returns:
            None: This method does not return a value.
        """
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def send(self, frame: bytes) -> None:
        """Write one complete frame to the socket.

        Args:
            frame (bytes): Complete ``MiniBleProto`` frame.

        Returns:
            None: This method does not return a value.

        Raises:
            LanError: If not connected or if the write fails.
        """
        if self._writer is None:
            msg = "LAN transport is not connected"
            raise LanError(msg)
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except OSError as error:
            msg = f"LAN write to {self._host}:{self._port} failed: {error}"
            raise LanError(msg) from error
        self._last_write = asyncio.get_running_loop().time()

    async def receive(self) -> bytes:
        """Wait for the next application frame (heartbeats are filtered out).

        Returns:
            bytes: Content of the received frame.

        Raises:
            LanError: If the link was lost.
        """
        item = await self._inbox.get()
        if isinstance(item, LanError):
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

    async def _handshake(self) -> None:
        """Send the handshake request and validate the response.

        Returns:
            None: This method does not return a value.

        Raises:
            LanError: If the response is missing, malformed, of the wrong command or negative.
        """
        identity = self._identity
        body = LanHandshakeRequest(
            uuid=identity.uuid,
            appVersion=identity.app_version,
            locale=identity.locale,
            deviceType=identity.device_type,
            sessionId=identity.session_id,
        ).SerializeToString(deterministic=True)
        request = CodeMaoRequest(LAN_HANDSHAKE_REQUEST_COMMAND, body)
        await self.send(self._codec.encode(request, _HANDSHAKE_SERIAL))
        try:
            async with asyncio.timeout(self._handshake_timeout):
                reply = self._codec.decode(await self.receive())
            result = parse_result(reply.body, LanHandshakeResponse)
        except TimeoutError as error:
            msg = f"no handshake response within {self._handshake_timeout:g}s"
            raise LanError(msg) from error
        except ProtocolError as error:
            msg = f"malformed handshake response: {error}"
            raise LanError(msg) from error
        if reply.command_id != LAN_HANDSHAKE_RESPONSE_COMMAND or not result.is_success:
            msg = (
                f"handshake refused (commandId={reply.command_id}, "
                f"resultCode={result.result_code}, message={result.message!r})"
            )
            raise LanError(msg)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Feed received bytes to the frame decoder until the link fails.

        Args:
            reader (asyncio.StreamReader): Read side of the connection.

        Returns:
            None: This method does not return a value.
        """
        try:
            while True:
                async with asyncio.timeout(self._read_timeout):
                    data = await reader.read(_READ_SIZE)
                if not data:
                    msg = "connection closed by the robot"
                    raise LanError(msg)  # noqa: TRY301 - handled below, same sink as I/O errors
                for content in self._decoder.feed(data):
                    if content not in LAN_HEARTBEAT_PAYLOADS:
                        self._inbox.put_nowait(content)
        except TimeoutError:
            self._inbox.put_nowait(LanError(f"no data from the robot for {self._read_timeout:g}s"))
        except OSError as error:
            self._inbox.put_nowait(LanError(f"LAN read failed: {error}"))
        except LanError as error:
            self._inbox.put_nowait(error)

    async def _heartbeat_loop(self) -> None:
        """Write a heartbeat whenever the writer has been idle for ``writer_idle`` seconds.

        Returns:
            None: This method does not return a value.
        """
        frame = LanFrameLayer().encode(LAN_HEARTBEAT_OUTBOUND_PONG)
        loop = asyncio.get_running_loop()
        while True:
            remaining = self._writer_idle - (loop.time() - self._last_write)
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            try:
                await self.send(frame)
            except LanError as error:
                self._inbox.put_nowait(error)
                return
