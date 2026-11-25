"""Protobuf messages of the Alpha Mini BLE envelope.

The messages are declared as a ``FileDescriptorProto`` and materialised with
the official ``protobuf`` runtime, so wire encoding (varints, tags, proto3
default omission, field ordering) is delegated to a maintained library rather
than re-implemented.

Scalar widths are inferred (varints encode identically for non-negative
values); field numbers and names come from the recovered sources. The module
exports :data:`BindOrSwitchWifiRequest`, :data:`MessageHeader`,
:data:`EnvelopeMessage` and :data:`BindOrSwitchWifiResponse` as runtime message
classes.
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

_PACKAGE = "alphamini.ble"
"""str: Protobuf package name prefixing every message in the descriptor pool."""


def _build_file() -> descriptor_pb2.FileDescriptorProto:
    """Build the file descriptor declaring the BLE envelope messages.

    Returns:
        descriptor_pb2.FileDescriptorProto: Descriptor for ``alpha_mini_ble.proto``.
    """
    proto = new_file("alpha_mini_ble.proto", _PACKAGE)

    request = proto.message_type.add()
    request.name = "BindOrSwitchWifiRequest"
    add_field(request, "data", 1, Field.TYPE_STRING)
    add_field(request, "versionCode", 2, Field.TYPE_INT32)

    header = proto.message_type.add()
    header.name = "MessageHeader"
    add_field(header, "commandId", 1, Field.TYPE_INT32)
    add_field(header, "sendSerial", 2, Field.TYPE_INT64)
    add_field(header, "responseSerial", 3, Field.TYPE_INT64)
    add_field(header, "versionCode", 4, Field.TYPE_INT32)
    add_field(header, "msgType", 5, Field.TYPE_INT32)

    response = proto.message_type.add()
    response.name = "BindOrSwitchWifiResponse"
    add_field(response, "data", 1, Field.TYPE_STRING)

    message = proto.message_type.add()
    message.name = "Message"
    add_field(message, "header", 1, Field.TYPE_MESSAGE, f".{_PACKAGE}.MessageHeader")
    add_field(message, "bodyData", 2, Field.TYPE_BYTES)
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


BindOrSwitchWifiRequest: type[Message] = _message_class("BindOrSwitchWifiRequest")
"""type[Message]: Layer 1: ``{data: string, versionCode: int32}``."""

MessageHeader: type[Message] = _message_class("MessageHeader")
"""type[Message]: Layer 2 header with ``commandId``, ``sendSerial``, ``responseSerial``,
``versionCode`` and ``msgType``."""

EnvelopeMessage: type[Message] = _message_class("Message")
"""type[Message]: Layer 3: ``{header: MessageHeader, bodyData: bytes}``."""

BindOrSwitchWifiResponse: type[Message] = _message_class("BindOrSwitchWifiResponse")
"""type[Message]: Response body ``{data: string}`` (field layout inferred from the report)."""
