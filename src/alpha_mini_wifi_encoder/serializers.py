"""JSON serialisation strategies for :class:`~.models.WifiPayload`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from alpha_mini_wifi_encoder.models import WifiPayload

_GSON_HTML_SAFE = str.maketrans(
    {
        "<": "\\u003c",
        ">": "\\u003e",
        "&": "\\u0026",
        "=": "\\u003d",
        "'": "\\u0027",
        "\u2028": "\\u2028",
        "\u2029": "\\u2029",
    },
)


class PayloadSerializer(Protocol):
    """Strategy turning a payload into the JSON text sent to the robot."""

    def serialize(self, payload: WifiPayload) -> str:
        """Serialise ``payload`` to JSON text."""
        ...


class CompactJsonSerializer:
    """Compact JSON (no spaces), raw UTF-8, no HTML escaping."""

    def serialize(self, payload: WifiPayload) -> str:
        """Serialise ``payload`` to compact JSON text."""
        return json.dumps(payload.to_wire_dict(), ensure_ascii=False, separators=(",", ":"))


class GsonCompatSerializer(CompactJsonSerializer):
    r"""Byte-compatible with ``new Gson().toJson(...)`` (``htmlSafe`` enabled).

    Gson escapes ``< > & = '`` (and U+2028/U+2029) as ``\uXXXX`` even though
    JSON does not require it, so passwords containing those characters differ
    from plain ``json.dumps`` output. Those characters can only occur inside
    string literals, hence translating the whole document is safe.
    """

    def serialize(self, payload: WifiPayload) -> str:
        """Serialise ``payload`` the way Gson's default instance does."""
        return super().serialize(payload).translate(_GSON_HTML_SAFE)
