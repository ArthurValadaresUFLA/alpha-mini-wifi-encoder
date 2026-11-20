"""Independent decoding helpers mirroring ``MiniBleProto.unpack`` and reassembly."""

from alpha_mini_wifi_encoder.protobuf_messages import (
    BindOrSwitchWifiRequest,
    EnvelopeMessage,
)


def reassemble(chunks: tuple[bytes, ...]) -> bytes:
    """Drop the 4-byte trailers and concatenate the chunk payloads."""
    return b"".join(chunk[:-4] for chunk in chunks)


def unpack(frame: bytes) -> bytes:
    """Validate a frame like ``MiniBleProto.unpack`` and return its content."""
    assert frame[:3] == b"\xfb\xbf\x01"
    length = int.from_bytes(frame[3:5], "big")
    assert frame[length] == 0xED
    assert length == len(frame) - 1
    return frame[5 : length - 1]


def decode_json_text(frame: bytes) -> tuple[str, int, int, int]:
    """Return ``(json, sendSerial, commandId, innerVersionCode)`` from a frame."""
    message = EnvelopeMessage.FromString(unpack(frame))
    request = BindOrSwitchWifiRequest.FromString(message.bodyData)  # type: ignore[attr-defined]
    header = message.header  # type: ignore[attr-defined]
    return request.data, header.sendSerial, header.commandId, request.versionCode  # type: ignore[attr-defined]
