import asyncio

import pytest

from alpha_mini_wifi_encoder.codemao import CodeMaoReply, CodeMaoRequest
from alpha_mini_wifi_encoder.constants import LAN_KICKED_COMMAND, CodeMaoCommand
from alpha_mini_wifi_encoder.errors import ChannelKickedError, ProtocolError, ReplyTimeoutError
from alpha_mini_wifi_encoder.session import CodeMaoSession
from tests.codemao_helpers import ScriptedTransport, default_responder, envelope


def test_full_programming_flow() -> None:
    async def scenario() -> ScriptedTransport:
        transport = ScriptedTransport()
        async with CodeMaoSession(transport) as session:
            handshake = await session.handshake("42", "kid", 3)
            assert handshake.is_success
            assert (handshake.robot_programme_version, handshake.nickname) == (7, "robot")
            assert (await session.start_program()).is_success
            assert (await session.stop_program()).is_success
            assert (await session.revert_origin()).is_success
        return transport

    transport = asyncio.run(scenario())
    assert (transport.connects, transport.closes) == (1, 1)
    assert [r.command_id for r in transport.sent] == [29, 33, 33, 28]
    assert [r.send_serial for r in transport.sent] == [1, 2, 3, 4]


def test_failure_result_is_reported_not_raised() -> None:
    async def scenario() -> bool:
        transport = ScriptedTransport(default_responder(frozenset({CodeMaoCommand.HANDSHAKE})))
        async with CodeMaoSession(transport) as session:
            result = await session.handshake("1", "n", 1)
        return result.is_success

    assert asyncio.run(scenario()) is False


def test_unsolicited_messages_are_reported_and_skipped() -> None:
    events: list[CodeMaoReply] = []

    def responder(request: CodeMaoReply) -> list[bytes]:
        noise = envelope(500, b"x", send_serial=77)
        return [noise, *default_responder()(request)]

    async def scenario() -> bool:
        async with CodeMaoSession(ScriptedTransport(responder), on_event=events.append) as session:
            return (await session.start_program()).is_success

    assert asyncio.run(scenario()) is True
    assert [e.command_id for e in events] == [500]


def test_unsolicited_messages_without_callback_are_dropped() -> None:
    def responder(request: CodeMaoReply) -> list[bytes]:
        return [envelope(500), *default_responder()(request)]

    async def scenario() -> bool:
        async with CodeMaoSession(ScriptedTransport(responder)) as session:
            return (await session.start_program()).is_success

    assert asyncio.run(scenario()) is True


def test_reply_without_response_serial_matches_by_command_id() -> None:
    def responder(request: CodeMaoReply) -> list[bytes]:
        return [envelope(request.command_id, b"\x08\x01")]

    async def scenario() -> bool:
        async with CodeMaoSession(ScriptedTransport(responder)) as session:
            return (await session.start_program()).is_success

    assert asyncio.run(scenario()) is True


def test_wrong_response_serial_is_not_an_answer() -> None:
    def responder(request: CodeMaoReply) -> list[bytes]:
        return [envelope(request.command_id, b"\x08\x01", response_serial=999)]

    async def scenario() -> None:
        async with CodeMaoSession(ScriptedTransport(responder), reply_timeout=0.05) as session:
            await session.start_program()

    with pytest.raises(ReplyTimeoutError, match="command 33"):
        asyncio.run(scenario())


def test_timeout_without_reply() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(lambda _request: [])
        async with CodeMaoSession(transport, reply_timeout=0.05) as session:
            await session.request(CodeMaoRequest(28, b""))

    with pytest.raises(ReplyTimeoutError):
        asyncio.run(scenario())


def test_kicked_during_request() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(lambda _r: [envelope(LAN_KICKED_COMMAND)])
        async with CodeMaoSession(transport) as session:
            await session.start_program()

    with pytest.raises(ChannelKickedError):
        asyncio.run(scenario())


def test_undecodable_reply_raises_protocol_error() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(lambda _r: [b"\xff\xff\xff"])
        async with CodeMaoSession(transport) as session:
            await session.start_program()

    with pytest.raises(ProtocolError):
        asyncio.run(scenario())


def test_listen_collects_events_until_the_deadline() -> None:
    events: list[CodeMaoReply] = []

    async def scenario() -> None:
        transport = ScriptedTransport()
        transport.inbox.put_nowait(envelope(600))
        transport.inbox.put_nowait(envelope(601))
        async with CodeMaoSession(transport, on_event=events.append) as session:
            await session.listen(0.05)

    asyncio.run(scenario())
    assert [e.command_id for e in events] == [600, 601]


def test_listen_without_callback_and_kicked() -> None:
    async def quiet() -> None:
        transport = ScriptedTransport()
        transport.inbox.put_nowait(envelope(600))
        async with CodeMaoSession(transport) as session:
            await session.listen(0.02)

    asyncio.run(quiet())

    async def kicked() -> None:
        transport = ScriptedTransport()
        transport.inbox.put_nowait(envelope(LAN_KICKED_COMMAND))
        async with CodeMaoSession(transport) as session:
            await session.listen(1)

    with pytest.raises(ChannelKickedError):
        asyncio.run(kicked())
