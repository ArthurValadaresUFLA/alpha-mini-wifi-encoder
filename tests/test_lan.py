import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest

from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, CodeMaoReply
from alpha_mini_wifi_encoder.codemao_messages import LanHandshakeRequest
from alpha_mini_wifi_encoder.constants import LAN_KICKED_COMMAND
from alpha_mini_wifi_encoder.errors import ChannelKickedError, LanError
from alpha_mini_wifi_encoder.frames import FrameStreamDecoder
from alpha_mini_wifi_encoder.lan import LanIdentity, LanTransport
from alpha_mini_wifi_encoder.session import CodeMaoSession
from tests.codemao_helpers import default_responder, envelope, frame_of, lan_frame_of, lan_ok

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@asynccontextmanager
async def serve(handler: Handler) -> AsyncIterator[int]:
    async def closing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Python 3.12's Server.wait_closed() waits for every connection to be closed.
        try:
            await handler(reader, writer)
        finally:
            writer.close()

    server = await asyncio.start_server(closing, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def read_frame(reader: asyncio.StreamReader, decoder: FrameStreamDecoder) -> bytes:
    while True:
        data = await reader.read(4096)
        if not data:
            raise ConnectionResetError  # EOF: stop the fake robot
        contents = decoder.feed(data)
        if contents:
            return contents[0]


def robot(
    *,
    handshake: bytes | None = None,
    log: list[CodeMaoReply] | None = None,
    extra: bytes = b"",
) -> Handler:
    """A robot that answers the handshake, then every known command."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        decoder = FrameStreamDecoder()
        try:
            first = CodeMaoCodec.decode(await read_frame(reader, decoder))
            if log is not None:
                log.append(first)
            writer.write(handshake if handshake is not None else lan_ok())
            if extra:
                writer.write(extra)
            await writer.drain()
            responder = default_responder()
            while True:
                request = CodeMaoCodec.decode(await read_frame(reader, decoder))
                if log is not None:
                    log.append(request)
                for content in responder(request):
                    writer.write(lan_frame_of(content))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            return

    return handle


def test_identity_defaults_are_fresh_and_overridable() -> None:
    first, second = LanIdentity(), LanIdentity()
    assert first.uuid != second.uuid
    assert first.session_id != second.session_id
    assert (first.client_version_name, first.client_version_code) == ("1.3.2", 1030)
    assert first.device_type == 1
    assert LanIdentity(uuid="u").uuid == "u"


def test_handshake_then_programming_session() -> None:
    log: list[CodeMaoReply] = []

    async def scenario() -> list[bool]:
        async with serve(robot(log=log)) as port:
            identity = LanIdentity(uuid="U", session_id="S", client_lan="pt", device_country="BR")
            transport = LanTransport("127.0.0.1", port, identity=identity)
            async with CodeMaoSession(transport) as session:
                handshake = await session.handshake("1", "n", 2)
                started = await session.start_program()
        return [handshake.is_success, started.is_success]

    assert asyncio.run(scenario()) == [True, True]
    hello = log[0]
    assert (hello.command_id, hello.send_serial) == (0, 0)
    body = LanHandshakeRequest.FromString(hello.body)
    fields = (
        body.clientLan,  # type: ignore[attr-defined]
        body.clientVersionName,  # type: ignore[attr-defined]
        body.clientVersionCode,  # type: ignore[attr-defined]
        body.deviceCountry,  # type: ignore[attr-defined]
        body.deviceType,  # type: ignore[attr-defined]
        body.uuid,  # type: ignore[attr-defined]
        body.sessionId,  # type: ignore[attr-defined]
    )
    assert fields == ("pt", "1.3.2", 1030, "BR", 1, "U", "S")
    assert [r.command_id for r in log[1:]] == [29, 33]


@pytest.mark.parametrize(
    ("handshake", "message"),
    [
        (lan_frame_of(envelope(7, b"")), "unexpected handshake response commandId=7"),
        (lan_frame_of(envelope(1000, b"\xff\xff")), "malformed handshake"),
    ],
)
def test_handshake_refusals(handshake: bytes, message: str) -> None:
    async def scenario() -> None:
        async with serve(robot(handshake=handshake)) as port:
            await LanTransport("127.0.0.1", port).connect()

    with pytest.raises(LanError, match=message):
        asyncio.run(scenario())


def test_handshake_timeout() -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        await asyncio.sleep(1)

    async def scenario() -> None:
        async with serve(silent) as port:
            await LanTransport("127.0.0.1", port, handshake_timeout=0.05).connect()

    with pytest.raises(LanError, match="no handshake response"):
        asyncio.run(scenario())


def test_connection_refused() -> None:
    async def scenario() -> None:
        async with serve(robot()) as port:
            pass
        await LanTransport("127.0.0.1", port, connect_timeout=1).connect()

    with pytest.raises(LanError, match="cannot connect"):
        asyncio.run(scenario())


def test_read_timeout_after_handshake() -> None:
    async def scenario() -> None:
        async with serve(robot()) as port:
            transport = LanTransport("127.0.0.1", port, read_timeout=0.1, writer_idle=10)
            async with transport:
                for _ in range(2):
                    with pytest.raises(LanError, match="no data from the robot"):
                        await transport.receive()

    asyncio.run(scenario())


def test_robot_closing_the_connection() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_frame(reader, FrameStreamDecoder())
        writer.write(lan_ok())
        await writer.drain()
        writer.close()

    async def scenario() -> None:
        async with serve(handler) as port:
            transport = LanTransport("127.0.0.1", port, writer_idle=10)
            async with transport:
                with pytest.raises(LanError, match="closed by the robot"):
                    await transport.receive()

    asyncio.run(scenario())


def test_heartbeats_are_sent_when_idle_and_received_ones_are_filtered() -> None:
    seen: list[bytes] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        decoder = FrameStreamDecoder()
        await read_frame(reader, decoder)
        writer.write(
            lan_ok() + lan_frame_of(b"\x00") + lan_frame_of(b"\x01") + lan_frame_of(envelope(55))
        )
        await writer.drain()
        while True:
            seen.append(await read_frame(reader, decoder))

    async def scenario() -> bytes:
        async with serve(handler) as port:
            transport = LanTransport("127.0.0.1", port, writer_idle=0.03, read_timeout=5)
            async with transport:
                content = await transport.receive()
                await asyncio.sleep(0.15)
            return content

    assert asyncio.run(scenario()) == envelope(55)
    assert seen
    assert set(seen) == {b"\x01"}  # the client answers with pong, never with ping


def test_kicked_surfaces_through_the_session() -> None:
    async def scenario() -> None:
        kicked = lan_frame_of(envelope(LAN_KICKED_COMMAND))
        async with serve(robot(extra=kicked)) as port:
            transport = LanTransport("127.0.0.1", port, writer_idle=10)
            async with CodeMaoSession(transport, reply_timeout=1) as session:
                await session.start_program()

    with pytest.raises(ChannelKickedError):
        asyncio.run(scenario())


def test_send_requires_connection() -> None:
    with pytest.raises(LanError, match="not connected"):
        asyncio.run(LanTransport("127.0.0.1", 1).send(b"x"))


class BrokenWriter:
    """Stream writer double that fails after ``ok_writes`` writes."""

    def __init__(self, ok_writes: int, *, close_error: bool = False) -> None:
        self.ok_writes = ok_writes
        self.close_error = close_error
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.ok_writes <= 0:
            raise BrokenPipeError
        self.ok_writes -= 1

    async def drain(self) -> None:
        return

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        if self.close_error:
            raise ConnectionResetError


def factory_with(
    writer: BrokenWriter, reader: asyncio.StreamReader
) -> Callable[..., Awaitable[tuple[asyncio.StreamReader, BrokenWriter]]]:
    async def open_connection(host: str, port: int) -> tuple[asyncio.StreamReader, BrokenWriter]:
        return reader, writer

    return open_connection


def test_write_failure_during_handshake() -> None:
    writer = BrokenWriter(0, close_error=True)

    async def scenario() -> None:
        transport = LanTransport(
            "h",
            1,
            connection_factory=factory_with(writer, asyncio.StreamReader()),  # type: ignore[arg-type]
        )
        await transport.connect()

    with pytest.raises(LanError, match="write to h:1 failed"):
        asyncio.run(scenario())
    assert writer.closed


def test_heartbeat_write_failure_is_reported_on_receive() -> None:
    async def scenario() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(lan_ok())
        writer = BrokenWriter(1)
        transport = LanTransport(
            "h",
            1,
            writer_idle=0.02,
            read_timeout=5,
            connection_factory=factory_with(writer, reader),  # type: ignore[arg-type]
        )
        async with transport:
            with pytest.raises(LanError, match="write to h:1 failed"):
                await transport.receive()

    asyncio.run(scenario())


def test_read_error_is_reported() -> None:
    class ExplodingReader(asyncio.StreamReader):
        async def read(self, n: int = -1) -> bytes:
            raise ConnectionResetError

    async def scenario() -> None:
        transport = LanTransport(
            "h",
            1,
            connection_factory=factory_with(BrokenWriter(5), ExplodingReader()),  # type: ignore[arg-type]
        )
        with pytest.raises(LanError, match="LAN read failed"):
            await transport.connect()

    asyncio.run(scenario())


def test_close_without_connection_is_a_no_op() -> None:
    async def scenario() -> None:
        transport = LanTransport("127.0.0.1", 1)
        await transport.close()
        await transport.close()

    asyncio.run(scenario())


def test_client_frames_carry_the_lan_checksum() -> None:
    raw: list[bytes] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw.append(await reader.read(4096))  # the handshake, as one TCP read
        writer.write(lan_ok())
        await writer.drain()
        raw.append(await reader.read(4096))  # the first heartbeat

    async def scenario() -> None:
        async with serve(handler) as port:
            transport = LanTransport("127.0.0.1", port, writer_idle=0.02, read_timeout=5)
            async with transport:
                await asyncio.sleep(0.1)

    asyncio.run(scenario())
    handshake, heartbeat = raw[0], raw[1]
    length = int.from_bytes(handshake[3:5], "big")
    assert handshake[length - 1] == (length + 2) & 0xFF
    assert handshake[length] == 0xED
    assert heartbeat.startswith(bytes.fromhex("fbbf0100070109ed"))  # pong 0x01, checksum 9


def test_handshake_result_is_exposed() -> None:
    async def scenario() -> tuple[int, str, str]:
        async with serve(robot()) as port:
            transport = LanTransport("127.0.0.1", port, writer_idle=10)
            assert (initial := transport.handshake_result) is None, initial
            async with transport:
                result = transport.handshake_result
                assert result is not None
                return (
                    result.bt_interface_version,
                    result.robot_soft_version_name,
                    result.session_id,
                )

    assert asyncio.run(scenario()) == (3, "2.0.1", "robot-session")
