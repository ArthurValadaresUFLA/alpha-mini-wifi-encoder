"""Protobuf messages of the CodeMao programming skill and of the LAN handshake.

Field numbers are confirmed for ``HandShakeRequest`` (1, 2, 3), ``ControlRunningProgramRequest``
(``isStart``) and for the LAN handshake pair (``BluetoothHandShakeRequest`` with 7 fields and
``BluetoothShakeResponse`` with 9, recovered from ``doc/lan_api.md``). The CodeMao *response*
layouts assume consecutive numbers in the order the report lists the fields. Nothing here was
validated on hardware.

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


_LAN_REQUEST_FIELDS: tuple[tuple[str, int], ...] = (
    ("clientLan", Field.TYPE_STRING),
    ("clientVersionName", Field.TYPE_STRING),
    ("clientVersionCode", Field.TYPE_INT32),
    ("deviceCountry", Field.TYPE_STRING),
    ("deviceType", Field.TYPE_INT32),
    ("uuid", Field.TYPE_STRING),
    ("sessionId", Field.TYPE_STRING),
)
"""tuple[tuple[str, int], ...]: ``BluetoothHandShakeRequest`` fields 1..7 (from the app)."""

_LAN_RESPONSE_FIELDS: tuple[tuple[str, int], ...] = (
    ("isSuccess", Field.TYPE_BOOL),
    ("resultCode", Field.TYPE_INT32),
    ("sysLan", Field.TYPE_STRING),
    ("sysCountry", Field.TYPE_STRING),
    ("robotSoftVersionName", Field.TYPE_STRING),
    ("btInterfaceVersion", Field.TYPE_INT32),
    ("androidFirmwareVersion", Field.TYPE_STRING),
    ("errorCode", Field.TYPE_INT32),
    ("sessionId", Field.TYPE_STRING),
)
"""tuple[tuple[str, int], ...]: ``BluetoothShakeResponse`` fields 1..9 (from the app)."""


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
    for number, (name, kind) in enumerate(_LAN_REQUEST_FIELDS, 1):
        add_field(lan_request, name, number, kind)

    lan_response = proto.message_type.add()
    lan_response.name = "LanHandshakeResponse"
    for number, (name, kind) in enumerate(_LAN_RESPONSE_FIELDS, 1):
        add_field(lan_response, name, number, kind)
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
"""type[Message]: ``BluetoothHandShakeRequest``, seven fields (``clientLan`` ... ``sessionId``)."""

LanHandshakeResponse: type[Message] = _message_class("LanHandshakeResponse")
"""type[Message]: ``BluetoothShakeResponse``, nine fields from ``isSuccess`` to ``sessionId``."""
