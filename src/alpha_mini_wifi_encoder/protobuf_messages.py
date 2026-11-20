"""Protobuf messages of the Alpha Mini BLE envelope.

The messages are declared as a ``FileDescriptorProto`` and materialised with
the official ``protobuf`` runtime, so wire encoding (varints, tags, proto3
default omission, field ordering) is delegated to a maintained library rather
than re-implemented.

Scalar widths are inferred (varints encode identically for non-negative
values); field numbers and names come from the recovered sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

if TYPE_CHECKING:
    from google.protobuf.message import Message

_PACKAGE = "alphamini.ble"
_Field = descriptor_pb2.FieldDescriptorProto


def _add_field(
    message: descriptor_pb2.DescriptorProto,
    name: str,
    number: int,
    field_type: int,
    type_name: str = "",
) -> None:
    field = message.field.add()
    field.name = name
    field.number = number
    field.type = field_type  # type: ignore[assignment]
    field.label = _Field.LABEL_OPTIONAL
    if type_name:
        field.type_name = type_name


def _build_file() -> descriptor_pb2.FileDescriptorProto:
    proto = descriptor_pb2.FileDescriptorProto()
    proto.name = "alpha_mini_ble.proto"
    proto.package = _PACKAGE
    proto.syntax = "proto3"

    request = proto.message_type.add()
    request.name = "BindOrSwitchWifiRequest"
    _add_field(request, "data", 1, _Field.TYPE_STRING)
    _add_field(request, "versionCode", 2, _Field.TYPE_INT32)

    header = proto.message_type.add()
    header.name = "MessageHeader"
    _add_field(header, "commandId", 1, _Field.TYPE_INT32)
    _add_field(header, "sendSerial", 2, _Field.TYPE_INT64)
    _add_field(header, "responseSerial", 3, _Field.TYPE_INT64)
    _add_field(header, "versionCode", 4, _Field.TYPE_INT32)
    _add_field(header, "msgType", 5, _Field.TYPE_INT32)

    response = proto.message_type.add()
    response.name = "BindOrSwitchWifiResponse"
    _add_field(response, "data", 1, _Field.TYPE_STRING)

    message = proto.message_type.add()
    message.name = "Message"
    _add_field(message, "header", 1, _Field.TYPE_MESSAGE, f".{_PACKAGE}.MessageHeader")
    _add_field(message, "bodyData", 2, _Field.TYPE_BYTES)
    return proto


_POOL = descriptor_pool.DescriptorPool()
_POOL.AddSerializedFile(_build_file().SerializeToString())


def _message_class(name: str) -> type[Message]:
    return message_factory.GetMessageClass(_POOL.FindMessageTypeByName(f"{_PACKAGE}.{name}"))


BindOrSwitchWifiRequest: type[Message] = _message_class("BindOrSwitchWifiRequest")
"""Layer 1: ``{data: string, versionCode: int32}``."""

MessageHeader: type[Message] = _message_class("MessageHeader")
"""Layer 2 header: ``commandId, sendSerial, responseSerial, versionCode, msgType``."""

EnvelopeMessage: type[Message] = _message_class("Message")
"""Layer 3: ``{header: MessageHeader, bodyData: bytes}``."""

BindOrSwitchWifiResponse: type[Message] = _message_class("BindOrSwitchWifiResponse")
"""Response body: ``{data: string}`` (field layout inferred from the report)."""
