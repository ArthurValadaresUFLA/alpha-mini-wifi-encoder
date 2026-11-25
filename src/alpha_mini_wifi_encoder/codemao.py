"""Commands, codec and typed results of the CodeMao programming skill.

:class:`CodeMaoRequest` is a command ready to be framed; the ``*_request`` builders create the
three commands recovered from the app (handshake ``29``, run/stop program ``33`` and revert
origin ``28``). :class:`CodeMaoCodec` wraps a request in the shared envelope and frame, and
decodes received frame contents into :class:`CodeMaoReply`. The ``parse_*`` functions turn a
reply body into :class:`CommandResult` or :class:`HandShakeResult`.

The same envelope and framing are used on BLE and on the LAN channel, so this module is
transport-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.protobuf.message import DecodeError

from alpha_mini_wifi_encoder.codemao_messages import (
    ControlRunningProgramRequest,
    ControlRunningProgramResponse,
    HandShakeRequest,
    HandShakeResponse,
    RevertOriginRequest,
    RevertOriginResponse,
)
from alpha_mini_wifi_encoder.constants import CODEMAO_HEADER_VERSION, CodeMaoCommand
from alpha_mini_wifi_encoder.errors import ProtocolError
from alpha_mini_wifi_encoder.layers import EncodingPipeline, FrameLayer, MessageLayer
from alpha_mini_wifi_encoder.protobuf_messages import EnvelopeMessage

if TYPE_CHECKING:
    from google.protobuf.message import Message


@dataclass(frozen=True, slots=True)
class CodeMaoRequest:
    """A command body together with the header values that identify it.

    Attributes:
        command_id (int): ``MessageHeader.commandId``.
        body (bytes): Serialised command-specific protobuf message.
        version (int): ``MessageHeader.versionCode``.
    """

    command_id: int
    body: bytes
    version: int = CODEMAO_HEADER_VERSION


@dataclass(frozen=True, slots=True)
class CodeMaoReply:
    """A decoded envelope received from the robot.

    Attributes:
        command_id (int): ``MessageHeader.commandId``.
        send_serial (int): ``MessageHeader.sendSerial`` chosen by the sender.
        response_serial (int): ``MessageHeader.responseSerial``; ``0`` when absent.
        version (int): ``MessageHeader.versionCode``.
        msg_type (int): ``MessageHeader.msgType``; ``0`` when absent.
        body (bytes): ``bodyData`` of the envelope.
    """

    command_id: int
    send_serial: int
    response_serial: int
    version: int
    msg_type: int
    body: bytes


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome reported by the robot for a command.

    Attributes:
        is_success (bool): ``isSuccess`` flag of the response.
        result_code (int): Robot-defined ``resultCode``.
        message (str): ``returnMsg`` text, possibly empty.
    """

    is_success: bool
    result_code: int
    message: str


@dataclass(frozen=True, slots=True)
class HandShakeResult(CommandResult):
    """Outcome of the programming-skill handshake.

    Attributes:
        robot_programme_version (int): Programme version supported by the robot.
        nickname (str): Nickname echoed by the robot.
        user_id (str): User id echoed by the robot.
    """

    robot_programme_version: int
    nickname: str
    user_id: str


def handshake_request(user_id: str, nickname: str, programme_version: int) -> CodeMaoRequest:
    """Build the request that starts the programming skill (``cmdId`` 29).

    Args:
        user_id (str): Identifier of the account driving the robot.
        nickname (str): Display name of the account.
        programme_version (int): Version of the Blockly programme format spoken by the client.

    Returns:
        CodeMaoRequest: The handshake command.
    """
    body = HandShakeRequest(
        nickName=nickname,
        userId=user_id,
        programmeVersion=programme_version,
    ).SerializeToString(deterministic=True)
    return CodeMaoRequest(CodeMaoCommand.HANDSHAKE, body)


def control_program_request(*, start: bool) -> CodeMaoRequest:
    """Build the request that starts or stops the running program (``cmdId`` 33).

    Args:
        start (bool): ``True`` to start the program, ``False`` to stop it.

    Returns:
        CodeMaoRequest: The run-control command.
    """
    body = ControlRunningProgramRequest(isStart=start).SerializeToString(deterministic=True)
    return CodeMaoRequest(CodeMaoCommand.CONTROL_RUNNING_PROGRAM, body)


def revert_origin_request() -> CodeMaoRequest:
    """Build the request that returns the robot to its origin pose (``cmdId`` 28).

    Returns:
        CodeMaoRequest: The revert-origin command.
    """
    body = RevertOriginRequest().SerializeToString(deterministic=True)
    return CodeMaoRequest(CodeMaoCommand.REVERT_ORIGIN, body)


class CodeMaoCodec:
    """Encode requests into frames and decode frame contents into replies."""

    @staticmethod
    def encode(request: CodeMaoRequest, send_serial: int) -> bytes:
        """Wrap ``request`` in the envelope and in a ``MiniBleProto`` frame.

        Args:
            request (CodeMaoRequest): Command to send.
            send_serial (int): Header ``sendSerial`` for this message.

        Returns:
            bytes: Complete frame, ``FB BF 01 <len BE> <envelope> 01 ED``.
        """
        pipeline = EncodingPipeline(
            (
                MessageLayer(
                    send_serial=send_serial,
                    command_id=request.command_id,
                    header_version=request.version,
                ),
                FrameLayer(),
            ),
        )
        return pipeline.run(request.body)[-1].data

    @staticmethod
    def decode(content: bytes) -> CodeMaoReply:
        """Decode the content of a received frame.

        Args:
            content (bytes): Frame content, as returned by ``unpack_frame``.

        Returns:
            CodeMaoReply: The decoded header values and body.

        Raises:
            ProtocolError: If ``content`` is not a valid envelope.
        """
        try:
            message = EnvelopeMessage.FromString(content)
        except DecodeError as error:
            msg = f"undecodable envelope: {error}"
            raise ProtocolError(msg) from error
        header: Any = message.header  # type: ignore[attr-defined]
        body: bytes = message.bodyData  # type: ignore[attr-defined]
        return CodeMaoReply(
            command_id=header.commandId,
            send_serial=header.sendSerial,
            response_serial=header.responseSerial,
            version=header.versionCode,
            msg_type=header.msgType,
            body=body,
        )


def _parse(message_type: type[Message], body: bytes) -> Any:  # noqa: ANN401 - dynamic message
    """Parse ``body`` as ``message_type``.

    Args:
        message_type (type[Message]): Runtime message class to parse.
        body (bytes): Serialised message.

    Returns:
        Any: The parsed message instance.

    Raises:
        ProtocolError: If ``body`` is not a valid serialisation of ``message_type``.
    """
    try:
        return message_type.FromString(body)
    except DecodeError as error:
        msg = f"undecodable {message_type.DESCRIPTOR.name}: {error}"
        raise ProtocolError(msg) from error


def parse_result(
    body: bytes, message_type: type[Message] = ControlRunningProgramResponse
) -> CommandResult:
    """Parse a response made of the ``isSuccess``/``resultCode``/``returnMsg`` triple.

    Args:
        body (bytes): Serialised response.
        message_type (type[Message]): Response class; ``ControlRunningProgramResponse`` and
            ``RevertOriginResponse`` share the same layout.

    Returns:
        CommandResult: The decoded outcome.

    Raises:
        ProtocolError: If ``body`` cannot be decoded.
    """
    parsed = _parse(message_type, body)
    return CommandResult(parsed.isSuccess, parsed.resultCode, parsed.returnMsg)


def parse_revert_origin_result(body: bytes) -> CommandResult:
    """Parse a ``RevertOriginResponse``.

    Args:
        body (bytes): Serialised response.

    Returns:
        CommandResult: The decoded outcome.

    Raises:
        ProtocolError: If ``body`` cannot be decoded.
    """
    return parse_result(body, RevertOriginResponse)


def parse_handshake_result(body: bytes) -> HandShakeResult:
    """Parse a ``HandShakeResponse``.

    Args:
        body (bytes): Serialised response.

    Returns:
        HandShakeResult: The decoded outcome with the robot's programme version.

    Raises:
        ProtocolError: If ``body`` cannot be decoded.
    """
    parsed = _parse(HandShakeResponse, body)
    return HandShakeResult(
        is_success=parsed.isSuccess,
        result_code=parsed.resultCode,
        message=parsed.returnMsg,
        robot_programme_version=parsed.robotProgrammeVersion,
        nickname=parsed.nickName,
        user_id=parsed.userId,
    )
