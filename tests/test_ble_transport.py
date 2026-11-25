import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from bleak.exc import BleakError

from alpha_mini_wifi_encoder.ble_transport import BleTransport
from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, handshake_request
from alpha_mini_wifi_encoder.constants import (
    HEARTBEAT_CHARACTERISTIC_UUID,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
)
from alpha_mini_wifi_encoder.errors import BleError
from alpha_mini_wifi_encoder.ids import FixedRandomId
from alpha_mini_wifi_encoder.session import CodeMaoSession
from tests.codemao_helpers import default_responder, envelope, frame_of
from tests.helpers import reassemble


class FakeClient:
    """Minimal bleak client double; ``notify`` emulates robot notifications."""

    instances: list["FakeClient"] = []

    def __init__(
        self, address: str, *, timeout: float, disconnected_callback: Callable[..., None]
    ) -> None:
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.writes: list[tuple[str, bytes]] = []
        self.notify_callback: Callable[[object, bytearray], None] | None = None
        self.connected = False
        self.fail_connect: Exception | None = None
        self.fail_notify: Exception | None = None
        self.fail_write: Exception | None = None
        self.fail_disconnect: Exception | None = None
        self.on_frame: Callable[[bytes], list[bytes]] | None = None
        self._pending = b""
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        if self.fail_connect:
            raise self.fail_connect
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        if self.fail_disconnect:
            raise self.fail_disconnect

    async def start_notify(self, uuid: str, callback: Callable[[object, bytearray], None]) -> None:
        assert uuid == NOTIFY_CHARACTERISTIC_UUID
        if self.fail_notify:
            raise self.fail_notify
        self.notify_callback = callback

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool | None = None) -> None:
        if self.fail_write:
            raise self.fail_write
        self.writes.append((uuid, bytes(data)))
        if uuid == WRITE_CHARACTERISTIC_UUID and self.on_frame:
            self._pending += bytes(data)[:-4]
            if self._pending.endswith(b"\x01\xed"):
                frame, self._pending = self._pending, b""
                for reply in self.on_frame(frame):
                    self.notify(reply)

    def notify(self, frame: bytes, *, size: int = 16, trailer: bytes = b"\x00\x01\x00\x01") -> None:
        assert self.notify_callback is not None
        for i in range(0, len(frame), size):
            self.notify_callback(object(), bytearray(frame[i : i + size] + trailer))


def make(**kwargs: Any) -> tuple[BleTransport, Callable[[], FakeClient]]:
    FakeClient.instances.clear()
    transport = BleTransport(
        "AA:BB",
        chunker=TrailerChunker(FixedRandomId(1)),
        client_factory=FakeClient,
        **kwargs,
    )
    return transport, lambda: FakeClient.instances[-1]


def test_session_over_fake_ble_link() -> None:
    transport, client = make()

    async def scenario() -> list[bool]:
        async with CodeMaoSession(transport) as session:
            fake = client()
            responder = default_responder()

            def on_frame(frame: bytes) -> list[bytes]:
                request = CodeMaoCodec.decode(frame[5:-2])
                return [frame_of(content) for content in responder(request)]

            fake.on_frame = on_frame
            first = await session.handshake("u", "n", 1)
            second = await session.start_program()
            return [first.is_success, second.is_success]

    assert asyncio.run(scenario()) == [True, True]
    fake = client()
    assert not fake.connected
    writes = [data for uuid, data in fake.writes if uuid == WRITE_CHARACTERISTIC_UUID]
    assert all(len(data) <= 20 for data in writes)
    first_frame = CodeMaoCodec.encode(handshake_request("u", "n", 1), 1)
    assert reassemble(tuple(writes[:2])) == first_frame[:32]


def test_receive_reassembles_fragmented_notifications() -> None:
    transport, client = make()

    async def scenario() -> bytes:
        await transport.connect()
        client().notify(frame_of(envelope(5, b"hello")), size=7)
        return await transport.receive()

    assert asyncio.run(scenario()) == envelope(5, b"hello")


def test_raw_notifications_without_trailer() -> None:
    transport, client = make(trailer_size=0)

    async def scenario() -> bytes:
        await transport.connect()
        client().notify(frame_of(b"abc"), size=100, trailer=b"")
        return await transport.receive()

    assert asyncio.run(scenario()) == b"abc"


def test_context_manager() -> None:
    transport, client = make()

    async def scenario() -> None:
        async with transport:
            assert client().connected

    asyncio.run(scenario())
    assert not client().connected


def test_disconnect_makes_receive_raise_repeatedly() -> None:
    transport, client = make()

    async def scenario() -> None:
        await transport.connect()
        client().disconnected_callback(client())
        for _ in range(2):
            with pytest.raises(BleError, match="lost"):
                await transport.receive()

    asyncio.run(scenario())


def test_callbacks_before_connect_are_ignored() -> None:
    transport, _ = make()
    transport._on_notification(object(), bytearray(b"x"))
    transport._on_disconnect(object())
    assert transport._inbox.empty()


@pytest.mark.parametrize("stage", ["connect", "notify"])
def test_connection_failures(stage: str) -> None:
    transport, client = make()
    original = FakeClient.__init__

    def patched(self: FakeClient, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        setattr(self, f"fail_{stage}", BleakError("boom"))

    FakeClient.__init__ = patched  # type: ignore[method-assign]
    try:
        with pytest.raises(BleError, match="connection to AA:BB failed"):
            asyncio.run(transport.connect())
    finally:
        FakeClient.__init__ = original  # type: ignore[method-assign]
    assert not client().connected


def test_send_requires_connection_and_reports_write_errors() -> None:
    transport, client = make()
    with pytest.raises(BleError, match="not connected"):
        asyncio.run(transport.send(b"x"))

    async def scenario() -> None:
        await transport.connect()
        client().fail_write = TimeoutError("slow")
        await transport.send(b"x")

    with pytest.raises(BleError, match="write to AA:BB failed"):
        asyncio.run(scenario())


def test_close_is_idempotent_and_tolerates_disconnect_errors() -> None:
    transport, client = make()

    async def scenario() -> None:
        await transport.connect()
        client().fail_disconnect = BleakError("gone")
        await transport.close()
        await transport.close()

    asyncio.run(scenario())


def test_heartbeat_is_written_periodically_and_stops_on_error() -> None:
    transport, client = make(heartbeat_interval=0.01, heartbeat_payload=b"\x07")

    async def scenario() -> None:
        await transport.connect()
        await asyncio.sleep(0.05)
        beats = [d for u, d in client().writes if u == HEARTBEAT_CHARACTERISTIC_UUID]
        assert beats
        assert set(beats) == {b"\x07"}
        client().fail_write = BleakError("link")
        await asyncio.sleep(0.03)
        await transport.close()

    asyncio.run(scenario())


def test_heartbeat_task_ends_when_client_is_gone() -> None:
    transport, _ = make(heartbeat_interval=0.01)

    async def scenario() -> None:
        await transport.connect()
        task = transport._heartbeat_task
        assert task is not None
        transport._client = None
        await asyncio.wait_for(task, 1)
        await transport.close()

    asyncio.run(scenario())
