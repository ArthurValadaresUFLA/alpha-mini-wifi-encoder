"""Commands, codec and typed results of the CodeMao programming skill.

:class:`CodeMaoRequest` is a command ready to be framed; the ``*_request`` builders create the
three commands recovered from the app (handshake ``29``, run/stop program ``33`` and revert
origin ``28``). :class:`CodeMaoCodec` wraps a request in the shared envelope and frame, and
decodes received frame contents into :class:`CodeMaoReply`. The ``parse_*`` functions turn a
reply body into :class:`CommandResult` or :class:`HandShakeResult`.

The same envelope is used on BLE and on the LAN channel; this module is transport-agnostic.
Note that ``protobuf_messages.MessageHeader`` declares the BLE serials as ``int64`` while the LAN
stack uses ``int32``: for the small values used here the varint encoding is identical, but the
two headers are distinct protocol definitions and must not be merged silently.
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
    LanHandshakeResponse,
    RevertOriginRequest,
    RevertOriginResponse,
)
from alpha_mini_wifi_encoder.constants import CODEMAO_HEADER_VERSION, CodeMaoCommand
from alpha_mini_wifi_encoder.errors import ProtocolError
from alpha_mini_wifi_encoder.layers import EncodingPipeline, FrameLayer, LanFrameLayer, MessageLayer
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


@dataclass(frozen=True, slots=True)
class LanHandshakeResult:
    """Outcome of the LAN handshake (``BluetoothShakeResponse``).

    Attributes:
        is_success (bool): ``isSuccess`` flag.
        result_code (int): Robot-defined ``resultCode``.
        error_code (int): ``errorCode``; non-zero values explain a refusal.
        session_id (str): ``sessionId`` returned by the robot.
        sys_lan (str): Robot system language.
        sys_country (str): Robot system country.
        robot_soft_version_name (str): Robot software version.
        bt_interface_version (int): Version of the robot's Bluetooth/LAN interface.
        android_firmware_version (str): Android firmware version of the robot.
    """

    is_success: bool
    result_code: int
    error_code: int
    session_id: str
    sys_lan: str
    sys_country: str
    robot_soft_version_name: str
    bt_interface_version: int
    android_firmware_version: str


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
    """Encode requests into frames and decode frame contents into replies.

    The envelope is shared by both channels; only the frame's closing bytes differ, so the codec
    is parameterised by the frame layer: the BLE tail ``01 ED`` or the LAN checksum.
    """

    def __init__(self, frame_layer: FrameLayer | None = None) -> None:
        """Create a codec.

        Args:
            frame_layer (FrameLayer | None): Frame layer closing the packets; BLE by default.

        Returns:
            None: This method does not return a value.
        """
        self._frame_layer = frame_layer or FrameLayer()

    @classmethod
    def for_lan(cls) -> CodeMaoCodec:
        """Create the codec for the TCP channel, whose frames carry a checksum byte.

        Returns:
            CodeMaoCodec: A codec using :class:`~.layers.LanFrameLayer`.
        """
        return cls(LanFrameLayer())

    def encode(self, request: CodeMaoRequest, send_serial: int) -> bytes:
        """Wrap ``request`` in the envelope and in a ``MiniBleProto`` frame.

        Args:
            request (CodeMaoRequest): Command to send.
            send_serial (int): Header ``sendSerial`` for this message.

        Returns:
            bytes: Complete frame, ``FB BF 01 <len BE> <envelope> <tail>``.
        """
        pipeline = EncodingPipeline(
            (
                MessageLayer(
                    send_serial=send_serial,
                    command_id=request.command_id,
                    header_version=request.version,
                ),
                self._frame_layer,
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


def parse_lan_handshake_result(body: bytes) -> LanHandshakeResult:
    """Parse the LAN ``BluetoothShakeResponse``.

    Args:
        body (bytes): Serialised response.

    Returns:
        LanHandshakeResult: The decoded outcome with the robot's versions and error code.

    Raises:
        ProtocolError: If ``body`` cannot be decoded.
    """
    parsed = _parse(LanHandshakeResponse, body)
    return LanHandshakeResult(
        is_success=parsed.isSuccess,
        result_code=parsed.resultCode,
        error_code=parsed.errorCode,
        session_id=parsed.sessionId,
        sys_lan=parsed.sysLan,
        sys_country=parsed.sysCountry,
        robot_soft_version_name=parsed.robotSoftVersionName,
        bt_interface_version=parsed.btInterfaceVersion,
        android_firmware_version=parsed.androidFirmwareVersion,
    )
