"""Request/response session for the CodeMao programming skill, independent of the transport.

:class:`FrameTransport` is the strategy implemented by the BLE and LAN transports: it moves whole
``MiniBleProto`` frames. :class:`CodeMaoSession` adds the ``sendSerial`` counter, correlates
replies with requests, reports unsolicited messages and exposes the typed commands
(:meth:`~CodeMaoSession.handshake`, :meth:`~CodeMaoSession.start_program`,
:meth:`~CodeMaoSession.stop_program` and :meth:`~CodeMaoSession.revert_origin`).

Reply correlation is inferred: a reply matches a request when its ``responseSerial`` equals the
request's ``sendSerial`` or, when the robot leaves ``responseSerial`` unset, when it carries the
same ``commandId``. This was not confirmed on hardware.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from typing import TYPE_CHECKING, Protocol, Self

from alpha_mini_wifi_encoder.codemao import (
    CodeMaoCodec,
    CodeMaoReply,
    CodeMaoRequest,
    CommandResult,
    HandShakeResult,
    control_program_request,
    handshake_request,
    parse_handshake_result,
    parse_result,
    parse_revert_origin_result,
    revert_origin_request,
)
from alpha_mini_wifi_encoder.constants import LAN_KICKED_COMMAND
from alpha_mini_wifi_encoder.errors import ChannelKickedError, ReplyTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

DEFAULT_REPLY_TIMEOUT_SECONDS = 10.0
"""float: Seconds to wait for the reply to one request."""


class FrameTransport(Protocol):
    """Strategy moving whole frames between the client and the robot."""

    async def connect(self) -> None:
        """Open the channel (and run any channel-level handshake).

        Returns:
            None: This method does not return a value.
        """
        ...

    async def close(self) -> None:
        """Close the channel; must be safe to call more than once.

        Returns:
            None: This method does not return a value.
        """
        ...

    async def send(self, frame: bytes) -> None:
        """Deliver one complete frame to the robot.

        Args:
            frame (bytes): Complete ``MiniBleProto`` frame.

        Returns:
            None: This method does not return a value.
        """
        ...

    async def receive(self) -> bytes:
        """Wait for the next complete frame and return its content.

        Returns:
            bytes: Content of the received frame (envelope bytes).
        """
        ...


class CodeMaoSession:
    """A programming-skill session over any :class:`FrameTransport`."""

    def __init__(
        self,
        transport: FrameTransport,
        *,
        reply_timeout: float = DEFAULT_REPLY_TIMEOUT_SECONDS,
        on_event: Callable[[CodeMaoReply], None] | None = None,
    ) -> None:
        """Create the session; nothing is opened until the context is entered.

        Args:
            transport (FrameTransport): Channel used to reach the robot.
            reply_timeout (float): Seconds to wait for each reply.
            on_event (Callable[[CodeMaoReply], None] | None): Called with every received
                message that does not answer the request in flight.

        Returns:
            None: This method does not return a value.
        """
        self._transport = transport
        self._reply_timeout = reply_timeout
        self._on_event = on_event
        self._codec = CodeMaoCodec()
        self._serials = itertools.count(1)

    async def __aenter__(self) -> Self:
        """Open the transport.

        Returns:
            Self: The connected session.
        """
        await self._transport.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the transport.

        Args:
            exc_type (type[BaseException] | None): Exception type raised in the block, if any.
            exc (BaseException | None): Exception raised in the block, if any.
            traceback (TracebackType | None): Traceback of that exception, if any.

        Returns:
            None: This method does not return a value.
        """
        await self._transport.close()

    async def request(self, request: CodeMaoRequest) -> CodeMaoReply:
        """Send ``request`` and wait for the reply that answers it.

        Messages that do not match are passed to ``on_event``.

        Args:
            request (CodeMaoRequest): Command to send.

        Returns:
            CodeMaoReply: The matching reply.

        Raises:
            ReplyTimeoutError: If no matching reply arrives within the reply timeout.
            ChannelKickedError: If the robot announces that another client took the channel.
        """
        serial = next(self._serials)
        await self._transport.send(self._codec.encode(request, serial))
        try:
            async with asyncio.timeout(self._reply_timeout):
                while True:
                    reply = await self._next_reply()
                    if self._answers(reply, request, serial):
                        return reply
                    self._emit(reply)
        except TimeoutError as error:
            msg = f"no reply to command {request.command_id} within {self._reply_timeout:g}s"
            raise ReplyTimeoutError(msg) from error

    async def handshake(
        self, user_id: str, nickname: str, programme_version: int
    ) -> HandShakeResult:
        """Start the programming skill (the app does this right after connecting).

        Args:
            user_id (str): Identifier of the account driving the robot.
            nickname (str): Display name of the account.
            programme_version (int): Blockly programme format version of the client.

        Returns:
            HandShakeResult: The robot's answer.
        """
        reply = await self.request(handshake_request(user_id, nickname, programme_version))
        return parse_handshake_result(reply.body)

    async def start_program(self) -> CommandResult:
        """Ask the robot to run the program it holds.

        Returns:
            CommandResult: The robot's answer.
        """
        reply = await self.request(control_program_request(start=True))
        return parse_result(reply.body)

    async def stop_program(self) -> CommandResult:
        """Ask the robot to stop the running program.

        Returns:
            CommandResult: The robot's answer.
        """
        reply = await self.request(control_program_request(start=False))
        return parse_result(reply.body)

    async def revert_origin(self) -> CommandResult:
        """Ask the robot to return to its origin pose.

        Returns:
            CommandResult: The robot's answer.
        """
        reply = await self.request(revert_origin_request())
        return parse_revert_origin_result(reply.body)

    async def listen(self, seconds: float) -> None:
        """Pass every message received during ``seconds`` to ``on_event``.

        Args:
            seconds (float): Duration to keep listening.

        Returns:
            None: This method does not return a value.

        Raises:
            ChannelKickedError: If the robot announces that another client took the channel.
        """
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                while True:
                    reply = await self._next_reply()
                    self._emit(reply)

    async def _next_reply(self) -> CodeMaoReply:
        """Receive and decode the next message, rejecting a takeover notice.

        Returns:
            CodeMaoReply: The next decoded message.

        Raises:
            ChannelKickedError: If the robot announces that another client took the channel.
        """
        reply = self._codec.decode(await self._transport.receive())
        if reply.command_id == LAN_KICKED_COMMAND:
            msg = "the robot reports that another client took over the channel"
            raise ChannelKickedError(msg)
        return reply

    @staticmethod
    def _answers(reply: CodeMaoReply, request: CodeMaoRequest, serial: int) -> bool:
        """Decide whether ``reply`` answers the request sent with ``serial``.

        Args:
            reply (CodeMaoReply): Received message.
            request (CodeMaoRequest): Request in flight.
            serial (int): ``sendSerial`` used for that request.

        Returns:
            bool: True if ``responseSerial`` equals ``serial``, or if it is unset and the
                command ids are equal.
        """
        if reply.response_serial:
            return reply.response_serial == serial
        return reply.command_id == request.command_id

    def _emit(self, reply: CodeMaoReply) -> None:
        """Report an unsolicited message to the event callback.

        Args:
            reply (CodeMaoReply): Message that does not answer the request in flight.

        Returns:
            None: This method does not return a value.
        """
        if self._on_event is not None:
            self._on_event(reply)
