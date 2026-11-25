"""Builders and a scripted transport for the programming-skill tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, CodeMaoReply
from alpha_mini_wifi_encoder.codemao_messages import (
    ControlRunningProgramResponse,
    HandShakeResponse,
    LanHandshakeResponse,
    RevertOriginResponse,
)
from alpha_mini_wifi_encoder.constants import CodeMaoCommand
from alpha_mini_wifi_encoder.layers import FrameLayer, unpack_frame
from alpha_mini_wifi_encoder.protobuf_messages import EnvelopeMessage, MessageHeader

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.protobuf.message import Message


def envelope(
    command_id: int,
    body: bytes = b"",
    *,
    response_serial: int = 0,
    send_serial: int = 1,
) -> bytes:
    """Serialise an envelope the way the robot would send it (frame content)."""
    header = MessageHeader(
        commandId=command_id,
        sendSerial=send_serial,
        responseSerial=response_serial,
        versionCode=1,
    )
    return EnvelopeMessage(header=header, bodyData=body).SerializeToString(deterministic=True)


def frame_of(content: bytes) -> bytes:
    """Wrap ``content`` in a ``MiniBleProto`` frame."""
    return FrameLayer().encode(content)


def lan_ok(*, success: bool = True, code: int = 0) -> bytes:
    """Build a LAN handshake response frame (commandId 1000)."""
    body = LanHandshakeResponse(
        isSuccess=success, resultCode=code, returnMsg=""
    ).SerializeToString()
    return frame_of(envelope(1000, body))


def result_body(message_type: type[Message], *, success: bool = True, code: int = 0) -> bytes:
    """Serialise a result-triple response."""
    return message_type(isSuccess=success, resultCode=code, returnMsg="").SerializeToString()


def default_responder(
    failing: frozenset[int] = frozenset(),
) -> Callable[[CodeMaoReply], list[bytes]]:
    """Build a responder that answers every known command, failing those in ``failing``."""

    def respond(request: CodeMaoReply) -> list[bytes]:
        ok = request.command_id not in failing
        if request.command_id == CodeMaoCommand.HANDSHAKE:
            body = HandShakeResponse(
                isSuccess=ok,
                resultCode=0 if ok else 5,
                returnMsg="" if ok else "busy",
                robotProgrammeVersion=7,
                nickName="robot",
                userId="42",
            ).SerializeToString()
        elif request.command_id == CodeMaoCommand.REVERT_ORIGIN:
            body = result_body(RevertOriginResponse, success=ok)
        else:
            body = result_body(ControlRunningProgramResponse, success=ok)
        return [envelope(request.command_id, body, response_serial=request.send_serial)]

    return respond


class ScriptedTransport:
    """In-memory :class:`~alpha_mini_wifi_encoder.session.FrameTransport`."""

    def __init__(
        self,
        responder: Callable[[CodeMaoReply], list[bytes]] | None = None,
    ) -> None:
        self.responder = responder or default_responder()
        self.sent: list[CodeMaoReply] = []
        self.inbox: asyncio.Queue[bytes | Exception] = asyncio.Queue()
        self.connects = 0
        self.closes = 0

    async def connect(self) -> None:
        self.connects += 1

    async def close(self) -> None:
        self.closes += 1

    async def send(self, frame: bytes) -> None:
        request = CodeMaoCodec.decode(unpack_frame(frame))
        self.sent.append(request)
        for content in self.responder(request):
            self.inbox.put_nowait(content)

    async def receive(self) -> bytes:
        item = await self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item
