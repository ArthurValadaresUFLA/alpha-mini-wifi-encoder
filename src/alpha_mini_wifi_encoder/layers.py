"""Encoding layers and the pipeline that chains them (Pipeline / Chain pattern).

:class:`EncodingLayer` defines the wrapping interface, :class:`LayerOutput` records each
intermediate result, and :class:`EncodingPipeline` applies layers in order. The concrete
layers (:class:`BindOrSwitchWifiLayer`, :class:`MessageLayer` and :class:`FrameLayer`) mirror the
protobuf, envelope and framing steps of the companion app. :func:`unpack_frame` validates the
resulting single frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from alpha_mini_wifi_encoder.constants import (
    BIND_REQUEST_VERSION_CODE,
    ENVELOPE_COMMAND_ID,
    FRAME_MAGIC,
    FRAME_OVERHEAD,
    FRAME_TAIL,
    FRAME_TYPE_SINGLE,
    HEADER_VERSION_CODE,
    LAN_CHECKSUM_OFFSET,
    MAX_FRAME_LENGTH_FIELD,
    MAX_SEND_SERIAL,
)
from alpha_mini_wifi_encoder.errors import FrameError, PayloadTooLargeError
from alpha_mini_wifi_encoder.protobuf_messages import (
    BindOrSwitchWifiRequest,
    EnvelopeMessage,
    MessageHeader,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class EncodingLayer(Protocol):
    """One wrapping step of the protocol stack."""

    name: str

    def encode(self, payload: bytes) -> bytes:
        """Wrap ``payload`` and return the bytes handed to the next layer.

        Args:
            payload (bytes): Bytes produced by the previous layer.

        Returns:
            bytes: Wrapped bytes to pass to the next layer.
        """
        ...


@dataclass(frozen=True, slots=True)
class LayerOutput:
    """Bytes produced by one layer, kept for inspection.

    Attributes:
        name (str): Human-readable layer name.
        data (bytes): Output of the layer.
    """

    name: str
    data: bytes


class BindOrSwitchWifiLayer:
    """Layer 1: wrap the JSON text in ``BindOrSwitchWifiRequest``."""

    name = "Layer 1 - BindOrSwitchWifiRequest"

    def __init__(self, version_code: int = BIND_REQUEST_VERSION_CODE) -> None:
        """Create the layer.

        Args:
            version_code (int): Value of ``versionCode`` (``168`` in the app).

        Returns:
            None: This function does not return a value.
        """
        self._version_code = version_code

    def encode(self, payload: bytes) -> bytes:
        """Wrap UTF-8 JSON ``payload``.

        Args:
            payload (bytes): UTF-8 encoded JSON document.

        Returns:
            bytes: Serialised ``BindOrSwitchWifiRequest`` message.
        """
        request = BindOrSwitchWifiRequest(data=payload.decode(), versionCode=self._version_code)
        return request.SerializeToString(deterministic=True)


class MessageLayer:
    """Layer 2+3: prepend ``MessageHeader`` and wrap everything in ``Message``."""

    name = "Layer 2/3 - Message{header, bodyData}"

    def __init__(
        self,
        send_serial: int = 1,
        command_id: int = ENVELOPE_COMMAND_ID,
        header_version: int = HEADER_VERSION_CODE,
    ) -> None:
        """Create the layer.

        Args:
            send_serial (int): Per-session monotonic counter (starts at 1 in the app).
            command_id (int): ``MessageHeader.commandId``.
            header_version (int): ``MessageHeader.versionCode``.

        Returns:
            None: This function does not return a value.

        Raises:
            ValueError: If ``send_serial`` is outside ``[0, MAX_SEND_SERIAL]``.
        """
        if not 0 <= send_serial <= MAX_SEND_SERIAL:
            msg = f"send_serial must be in [0, {MAX_SEND_SERIAL}]"
            raise ValueError(msg)
        self._send_serial = send_serial
        self._command_id = command_id
        self._header_version = header_version

    def encode(self, payload: bytes) -> bytes:
        """Wrap the layer-1 bytes as ``bodyData``.

        Args:
            payload (bytes): Serialised layer-1 message.

        Returns:
            bytes: Serialised envelope with header and body data.
        """
        header = MessageHeader(
            commandId=self._command_id,
            sendSerial=self._send_serial,
            versionCode=self._header_version,
        )
        message = EnvelopeMessage(header=header, bodyData=payload)
        return message.SerializeToString(deterministic=True)


class FrameLayer:
    """Layer 4: ``MiniBleProto.pack`` single-frame container."""

    name = "Layer 4 - MiniBleProto frame"

    def encode(self, payload: bytes) -> bytes:
        """Return ``FB BF 01 <len BE> <payload> 01 ED``.

        The length field holds the index of the final ``0xED`` byte, i.e.
        ``len(payload) + 6`` (total frame size minus one).

        Args:
            payload (bytes): Envelope bytes to wrap in a frame.

        Returns:
            bytes: Complete single-frame ``MiniBleProto`` container.

        Raises:
            PayloadTooLargeError: If the length no longer fits in 16 bits.
        """
        length = len(payload) + FRAME_OVERHEAD - 1
        if length > MAX_FRAME_LENGTH_FIELD:
            msg = f"frame length {length} exceeds {MAX_FRAME_LENGTH_FIELD}"
            raise PayloadTooLargeError(msg)
        return (
            FRAME_MAGIC
            + bytes([FRAME_TYPE_SINGLE])
            + length.to_bytes(2, "big")
            + payload
            + self._tail(length)
        )

    def _tail(self, length: int) -> bytes:
        """Return the two closing bytes of the frame.

        Args:
            length (int): Value of the frame's length field.

        Returns:
            bytes: The constant BLE tail ``01 ED``.
        """
        del length
        return FRAME_TAIL


class LanFrameLayer(FrameLayer):
    """Layer 4 for TCP: same container, but the byte before ``ED`` is a checksum."""

    name = "Layer 4 - MiniBleProto frame (LAN checksum)"

    def _tail(self, length: int) -> bytes:
        """Return ``<checksum> ED`` with ``checksum = (length + 2) & 0xFF``.

        Args:
            length (int): Value of the frame's length field.

        Returns:
            bytes: The checksum byte followed by the ``0xED`` terminator.
        """
        return bytes([(length + LAN_CHECKSUM_OFFSET) & 0xFF, FRAME_TAIL[-1]])


def unpack_frame(frame: bytes) -> bytes:
    """Validate a single frame like ``MiniBleProto.unpack`` and return its content.

    Args:
        frame (bytes): Complete frame, ``FB BF 01 <len BE> <content> 01 ED``.

    Returns:
        bytes: The content between the length field and the tail.

    Raises:
        FrameError: If the magic, type, length or terminator is invalid.
    """
    if len(frame) < FRAME_OVERHEAD or frame[:2] != FRAME_MAGIC or frame[2] != FRAME_TYPE_SINGLE:
        msg = "not a MiniBleProto single frame"
        raise FrameError(msg)
    length = int.from_bytes(frame[3:5], "big")
    if length != len(frame) - 1 or frame[length] != FRAME_TAIL[-1]:
        msg = "inconsistent frame length or missing terminator"
        raise FrameError(msg)
    return frame[5 : length - 1]


class EncodingPipeline:
    """Runs a payload through an ordered sequence of layers."""

    def __init__(self, layers: Sequence[EncodingLayer]) -> None:
        """Create the pipeline.

        Args:
            layers (Sequence[EncodingLayer]): Layers in application order (innermost first).

        Returns:
            None: This function does not return a value.
        """
        self._layers = tuple(layers)

    def run(self, payload: bytes) -> tuple[LayerOutput, ...]:
        """Apply every layer and return each intermediate output.

        Args:
            payload (bytes): Input of the first layer.

        Returns:
            tuple[LayerOutput, ...]: One output per layer; the last is the final frame.
        """
        outputs: list[LayerOutput] = []
        current = payload
        for layer in self._layers:
            current = layer.encode(current)
            outputs.append(LayerOutput(layer.name, current))
        return tuple(outputs)
