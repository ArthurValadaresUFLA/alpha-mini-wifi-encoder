"""Helpers to declare protobuf messages at runtime with the official ``protobuf`` runtime.

Messages are described with a ``FileDescriptorProto`` and materialised from a private
:class:`~google.protobuf.descriptor_pool.DescriptorPool`, so wire encoding (varints, tags,
proto3 default omission, field ordering) stays delegated to a maintained library.

The module exports the :data:`Field` alias, :func:`add_field`, :func:`new_file`,
:func:`build_pool` and :func:`message_class`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

if TYPE_CHECKING:
    from google.protobuf.message import Message

Field = descriptor_pb2.FieldDescriptorProto
"""type[descriptor_pb2.FieldDescriptorProto]: Short alias for field descriptor builders."""


def add_field(
    message: descriptor_pb2.DescriptorProto,
    name: str,
    number: int,
    field_type: int,
    type_name: str = "",
) -> None:
    """Append one optional field to a message descriptor.

    Args:
        message (descriptor_pb2.DescriptorProto): Message descriptor to extend.
        name (str): Field name.
        number (int): Field number used on the wire.
        field_type (int): ``FieldDescriptorProto`` type constant.
        type_name (str): Fully qualified type for message fields, empty otherwise.

    Returns:
        None: This function does not return a value.
    """
    field = message.field.add()
    field.name = name
    field.number = number
    field.type = field_type  # type: ignore[assignment]
    field.label = Field.LABEL_OPTIONAL
    if type_name:
        field.type_name = type_name


def new_file(name: str, package: str) -> descriptor_pb2.FileDescriptorProto:
    """Create an empty proto3 file descriptor.

    Args:
        name (str): Virtual file name, unique within the pool.
        package (str): Protobuf package prefixing every message.

    Returns:
        descriptor_pb2.FileDescriptorProto: Descriptor ready to receive message types.
    """
    proto = descriptor_pb2.FileDescriptorProto()
    proto.name = name
    proto.package = package
    proto.syntax = "proto3"
    return proto


def build_pool(proto: descriptor_pb2.FileDescriptorProto) -> descriptor_pool.DescriptorPool:
    """Register ``proto`` in a fresh, private descriptor pool.

    Args:
        proto (descriptor_pb2.FileDescriptorProto): Complete file descriptor.

    Returns:
        descriptor_pool.DescriptorPool: Pool that resolves the messages of ``proto``.
    """
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(proto.SerializeToString())
    return pool


def message_class(
    pool: descriptor_pool.DescriptorPool,
    package: str,
    name: str,
) -> type[Message]:
    """Retrieve a runtime message class from ``pool``.

    Args:
        pool (descriptor_pool.DescriptorPool): Pool built by :func:`build_pool`.
        package (str): Protobuf package of the message.
        name (str): Message name within ``package``.

    Returns:
        type[Message]: Runtime class implementing the named message.
    """
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(f"{package}.{name}"))
