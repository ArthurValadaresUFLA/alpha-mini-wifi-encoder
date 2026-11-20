"""Splitting a frame into GATT writes (``MiniBleProto.devide``).

:class:`ChunkingStrategy` declares the interface used by the encoder, and
:class:`TrailerChunker` implements the app's scheme of 16-byte frame slices
followed by a two-byte random id and a two-byte serial number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from alpha_mini_wifi_encoder.constants import GATT_CHUNK_PAYLOAD_SIZE
from alpha_mini_wifi_encoder.errors import PayloadTooLargeError

if TYPE_CHECKING:
    from alpha_mini_wifi_encoder.ids import RandomIdProvider

_MAX_CHUNKS = 0xFFFF
"""int: Largest chunk count representable by the 16-bit serial number."""


class ChunkingStrategy(Protocol):
    """Strategy turning a frame into the list of GATT write values."""

    def split(self, frame: bytes) -> tuple[bytes, ...]:
        """Split ``frame`` into write values.

        Args:
            frame (bytes): Complete frame to split.

        Returns:
            tuple[bytes, ...]: Ordered values for the GATT write requests.
        """
        ...


class TrailerChunker:
    """16-byte slices, each followed by ``randomId`` and ``serialNo`` (big-endian)."""

    def __init__(
        self,
        random_ids: RandomIdProvider,
        chunk_size: int = GATT_CHUNK_PAYLOAD_SIZE,
    ) -> None:
        """Create the chunker.

        Args:
            random_ids (RandomIdProvider): Provider of the per-message random id.
            chunk_size (int): Frame bytes per write.

        Returns:
            None: This function does not return a value.

        Raises:
            ValueError: If ``chunk_size`` is not positive.
        """
        if chunk_size <= 0:
            msg = "chunk_size must be positive"
            raise ValueError(msg)
        self._random_ids = random_ids
        self._chunk_size = chunk_size

    def split(self, frame: bytes) -> tuple[bytes, ...]:
        """Split ``frame``; the random id is drawn once per message.

        Args:
            frame (bytes): Complete frame to split.

        Returns:
            tuple[bytes, ...]: Frame slices, each with a random id and a serial number.

        Raises:
            PayloadTooLargeError: If the serial number would overflow 16 bits.
        """
        random_id = self._random_ids.next_id().to_bytes(2, "big")
        slices = [frame[i : i + self._chunk_size] for i in range(0, len(frame), self._chunk_size)]
        if len(slices) > _MAX_CHUNKS:
            msg = f"{len(slices)} chunks exceed the 16-bit serial number"
            raise PayloadTooLargeError(msg)
        return tuple(
            data + random_id + serial.to_bytes(2, "big") for serial, data in enumerate(slices, 1)
        )
