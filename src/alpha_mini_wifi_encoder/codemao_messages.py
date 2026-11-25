"""Protobuf messages of the CodeMao programming skill and of the LAN handshake.

Field *names* and the declaration order come from the analysis report. Field *numbers* are
confirmed only for ``HandShakeRequest`` (1, 2, 3) and ``ControlRunningProgramRequest``
(``isStart``); every response layout and the LAN handshake layout assume consecutive numbers
in the order the report lists the fields. Nothing here was validated on hardware.

The module exports the runtime message classes :data:`HandShakeRequest`,
:data:`HandShakeResponse`, :data:`ControlRunningProgramRequest`,
:data:`ControlRunningProgramResponse`, :data:`RevertOriginRequest`,
:data:`RevertOriginResponse`, :data:`LanHandshakeRequest` and :data:`LanHandshakeResponse`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha_mini_wifi_encoder.protobuf_builder import (
    Field,
    add_field,
    build_pool,
    message_class,
    new_file,
)

if TYPE_CHECKING:
    from google.protobuf import descriptor_pb2
    from google.protobuf.message import Message

_PACKAGE = "alphamini.codemao"
"""str: Protobuf package name prefixing every message in the descriptor pool."""


def _add_result_fields(message: descriptor_pb2.DescriptorProto) -> int:
    """Declare the ``isSuccess``/``resultCode``/``returnMsg`` triple shared by responses.

    Args:
        message (descriptor_pb2.DescriptorProto): Response descriptor to extend.

    Returns:
        int: The next free field number.
    """
    add_field(message, "isSuccess", 1, Field.TYPE_BOOL)
    add_field(message, "resultCode", 2, Field.TYPE_INT32)
    add_field(message, "returnMsg", 3, Field.TYPE_STRING)
    return 4


def _build_file() -> descriptor_pb2.FileDescriptorProto:
    """Build the file descriptor declaring the CodeMao and LAN handshake messages.

    Returns:
        descriptor_pb2.FileDescriptorProto: Descriptor for ``alpha_mini_codemao.proto``.
    """
    proto = new_file("alpha_mini_codemao.proto", _PACKAGE)

    request = proto.message_type.add()
    request.name = "HandShakeRequest"
    add_field(request, "nickName", 1, Field.TYPE_STRING)
    add_field(request, "userId", 2, Field.TYPE_STRING)
    add_field(request, "programmeVersion", 3, Field.TYPE_INT32)

    response = proto.message_type.add()
    response.name = "HandShakeResponse"
    next_number = _add_result_fields(response)
    add_field(response, "robotProgrammeVersion", next_number, Field.TYPE_INT32)
    add_field(response, "nickName", next_number + 1, Field.TYPE_STRING)
    add_field(response, "userId", next_number + 2, Field.TYPE_STRING)

    control = proto.message_type.add()
    control.name = "ControlRunningProgramRequest"
    add_field(control, "isStart", 1, Field.TYPE_BOOL)

    control_response = proto.message_type.add()
    control_response.name = "ControlRunningProgramResponse"
    _add_result_fields(control_response)

    proto.message_type.add().name = "RevertOriginRequest"

    revert_response = proto.message_type.add()
    revert_response.name = "RevertOriginResponse"
    _add_result_fields(revert_response)

    lan_request = proto.message_type.add()
    lan_request.name = "LanHandshakeRequest"
    for number, name in enumerate(("uuid", "appVersion", "locale", "deviceType", "sessionId"), 1):
        add_field(lan_request, name, number, Field.TYPE_STRING)

    lan_response = proto.message_type.add()
    lan_response.name = "LanHandshakeResponse"
    _add_result_fields(lan_response)
    return proto


_POOL = build_pool(_build_file())
"""descriptor_pool.DescriptorPool: Pool holding the dynamically built file descriptor."""


def _message_class(name: str) -> type[Message]:
    """Retrieve a generated message class from the shared descriptor pool.

    Args:
        name (str): Message name within :data:`_PACKAGE`.

    Returns:
        type[Message]: Runtime class implementing the named message.
    """
    return message_class(_POOL, _PACKAGE, name)


HandShakeRequest: type[Message] = _message_class("HandShakeRequest")
"""type[Message]: ``{nickName: string, userId: string, programmeVersion: int32}``."""

HandShakeResponse: type[Message] = _message_class("HandShakeResponse")
"""type[Message]: Result triple plus ``robotProgrammeVersion``, ``nickName`` and ``userId``."""

ControlRunningProgramRequest: type[Message] = _message_class("ControlRunningProgramRequest")
"""type[Message]: ``{isStart: bool}``."""

ControlRunningProgramResponse: type[Message] = _message_class("ControlRunningProgramResponse")
"""type[Message]: Result triple ``{isSuccess, resultCode, returnMsg}``."""

RevertOriginRequest: type[Message] = _message_class("RevertOriginRequest")
"""type[Message]: Empty request body."""

RevertOriginResponse: type[Message] = _message_class("RevertOriginResponse")
"""type[Message]: Result triple ``{isSuccess, resultCode, returnMsg}``."""

LanHandshakeRequest: type[Message] = _message_class("LanHandshakeRequest")
"""type[Message]: ``{uuid, appVersion, locale, deviceType, sessionId}`` (all strings)."""

LanHandshakeResponse: type[Message] = _message_class("LanHandshakeResponse")
"""type[Message]: Result triple ``{isSuccess, resultCode, returnMsg}``."""
