"""Incremental decoding of ``MiniBleProto`` frames from a byte stream.

TCP and GATT notifications deliver frames in arbitrary pieces. :class:`FrameStreamDecoder`
buffers those pieces, resynchronises on the ``FB BF`` marker after garbage, and yields the
content of each complete, valid frame.
"""

from __future__ import annotations

from alpha_mini_wifi_encoder.constants import FRAME_MAGIC
from alpha_mini_wifi_encoder.errors import FrameError
from alpha_mini_wifi_encoder.layers import unpack_frame

_HEADER_SIZE = len(FRAME_MAGIC) + 1 + 2
"""int: Bytes needed to read the length field: magic, type and two length bytes."""


class FrameStreamDecoder:
    """Stateful decoder turning arbitrary byte pieces into frame contents."""

    def __init__(self) -> None:
        """Create an empty decoder.

        Returns:
            None: This method does not return a value.
        """
        self._buffer = bytearray()

    @property
    def pending(self) -> int:
        """Report how many buffered bytes still wait for the rest of their frame.

        Returns:
            int: Size of the internal buffer in bytes.
        """
        return len(self._buffer)

    def feed(self, data: bytes) -> list[bytes]:
        """Append ``data`` and return the content of every frame it completes.

        Bytes before a frame marker, and frames that fail validation, are discarded.

        Args:
            data (bytes): Next piece of the stream.

        Returns:
            list[bytes]: Contents (without markers, length and tail) of the completed frames,
                in stream order; empty when no frame is complete yet.
        """
        self._buffer.extend(data)
        contents: list[bytes] = []
        while (content := self._next()) is not None:
            contents.append(content)
        return contents

    def _next(self) -> bytes | None:
        """Extract the next valid frame from the buffer, if one is complete.

        Returns:
            bytes | None: Frame content, or ``None`` when more bytes are required.
        """
        while True:
            start = self._buffer.find(FRAME_MAGIC)
            if start == -1:
                del self._buffer[: max(len(self._buffer) - len(FRAME_MAGIC) + 1, 0)]
                return None
            del self._buffer[:start]
            if len(self._buffer) < _HEADER_SIZE:
                return None
            total = int.from_bytes(self._buffer[3:5], "big") + 1
            if len(self._buffer) < total:
                return None
            candidate = bytes(self._buffer[:total])
            try:
                content = unpack_frame(candidate)
            except FrameError:
                del self._buffer[:1]
                continue
            del self._buffer[:total]
            return content
