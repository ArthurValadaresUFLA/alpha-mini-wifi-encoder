"""Encoding layers and the pipeline that chains them (Pipeline / Chain pattern)."""

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
        """Wrap ``payload`` and return the bytes handed to the next layer."""
        ...


@dataclass(frozen=True, slots=True)
class LayerOutput:
    """Bytes produced by one layer, kept for inspection.

    Attributes:
        name: Human-readable layer name.
        data: Output of the layer.
    """

    name: str
    data: bytes


class BindOrSwitchWifiLayer:
    """Layer 1: wrap the JSON text in ``BindOrSwitchWifiRequest``."""

    name = "Layer 1 - BindOrSwitchWifiRequest"

    def __init__(self, version_code: int = BIND_REQUEST_VERSION_CODE) -> None:
        """Create the layer.

        Args:
            version_code: Value of ``versionCode`` (``168`` in the app).
        """
        self._version_code = version_code

    def encode(self, payload: bytes) -> bytes:
        """Wrap UTF-8 JSON ``payload``."""
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
            send_serial: Per-session monotonic counter (starts at 1 in the app).
            command_id: ``MessageHeader.commandId``.
            header_version: ``MessageHeader.versionCode``.

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
        """Wrap the layer-1 bytes as ``bodyData``."""
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
            + FRAME_TAIL
        )


def unpack_frame(frame: bytes) -> bytes:
    """Validate a single frame like ``MiniBleProto.unpack`` and return its content.

    Args:
        frame: Complete frame, ``FB BF 01 <len BE> <content> 01 ED``.

    Returns:
        The content between the length field and the tail.

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
            layers: Layers in application order (innermost first).
        """
        self._layers = tuple(layers)

    def run(self, payload: bytes) -> tuple[LayerOutput, ...]:
        """Apply every layer and return each intermediate output.

        Args:
            payload: Input of the first layer.

        Returns:
            One :class:`LayerOutput` per layer; the last one is the final frame.
        """
        outputs: list[LayerOutput] = []
        current = payload
        for layer in self._layers:
            current = layer.encode(current)
            outputs.append(LayerOutput(layer.name, current))
        return tuple(outputs)
