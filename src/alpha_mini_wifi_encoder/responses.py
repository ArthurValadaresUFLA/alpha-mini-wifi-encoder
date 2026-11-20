"""Best-effort decoding of the robot's notifications.

Assumptions (inferred from the analysis report, not confirmed on hardware):
notifications reuse the ``MiniBleProto`` frame, may carry the same 4-byte
``randomId``/``serialNo`` trailer per fragment, and the response body is a
``BindOrSwitchWifiResponse`` whose field 1 is a JSON string with ``co``/``ec``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.protobuf.message import DecodeError

from alpha_mini_wifi_encoder.constants import FRAME_MAGIC, GATT_CHUNK_TRAILER_SIZE
from alpha_mini_wifi_encoder.errors import FrameError
from alpha_mini_wifi_encoder.layers import unpack_frame
from alpha_mini_wifi_encoder.protobuf_messages import BindOrSwitchWifiResponse, EnvelopeMessage

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

ERROR_COMMAND = -1

RESPONSE_MEANINGS: dict[int, str] = {
    101: "Wi-Fi list",
    102: "Wi-Fi information accepted",
    103: "Client id accepted",
    104: "BLE connection acknowledged",
    105: "Network state",
    108: "4G opened",
    109: "Bind result acknowledged",
    110: "Wi-Fi scan stopped",
    111: "Code challenge",
}


@dataclass(frozen=True, slots=True)
class WifiResponse:
    """One decoded response.

    Attributes:
        json_text: Raw JSON string of the response body.
        command: ``co`` value, if present.
        error_code: ``ec`` value, if present.
    """

    json_text: str
    command: int | None
    error_code: int | None

    @property
    def is_error(self) -> bool:
        """Whether the robot reported an error (``co`` = -1)."""
        return self.command == ERROR_COMMAND

    def describe(self) -> str:
        """Return a short human-readable description."""
        if self.is_error:
            return f"Error reported by the robot (ec={self.error_code})"
        meaning = RESPONSE_MEANINGS.get(self.command, "Unknown response") if self.command else None
        return f"{meaning} (co={self.command})" if meaning else "Unrecognised response"


def _iter_frames(data: bytes) -> Iterator[bytes]:
    """Yield every valid frame found in ``data`` (tolerates leading garbage)."""
    position = data.find(FRAME_MAGIC)
    while position != -1 and position + 5 <= len(data):
        length = int.from_bytes(data[position + 3 : position + 5], "big")
        candidate = data[position : position + length + 1]
        try:
            unpack_frame(candidate)
        except FrameError:
            position = data.find(FRAME_MAGIC, position + 1)
            continue
        yield candidate
        position = data.find(FRAME_MAGIC, position + length + 1)


def _parse_body(frame: bytes) -> WifiResponse | None:
    try:
        message = EnvelopeMessage.FromString(unpack_frame(frame))
        body = BindOrSwitchWifiResponse.FromString(message.bodyData)  # type: ignore[attr-defined]
        text: str = body.data  # type: ignore[attr-defined]
        document: Any = json.loads(text)
    except (DecodeError, FrameError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    command = document.get("co")
    error_code = document.get("ec")
    return WifiResponse(
        text,
        command if isinstance(command, int) else None,
        error_code if isinstance(error_code, int) else None,
    )


def decode_notifications(notifications: Sequence[bytes]) -> tuple[WifiResponse, ...]:
    """Decode the notifications received after a write.

    Both layouts are tried (fragments with a 4-byte trailer, then raw fragments)
    and the interpretation that yields more responses wins; ties go to the
    trailer layout.

    Args:
        notifications: Raw GATT notification values, in arrival order.

    Returns:
        Every response that could be decoded (possibly empty).
    """
    best: tuple[WifiResponse, ...] = ()
    for strip in (True, False):
        stream = b"".join(n[:-GATT_CHUNK_TRAILER_SIZE] if strip else n for n in notifications)
        responses = tuple(r for f in _iter_frames(stream) if (r := _parse_body(f)) is not None)
        if len(responses) > len(best):
            best = responses
    return best
