"""Providers for the per-message random identifier of GATT chunks.

:class:`RandomIdProvider` defines the strategy interface shared by the chunking
layer. :class:`FixedRandomId` returns a constant id for reproducible output,
while :class:`SystemRandomId` draws from the operating system's entropy source
in the same way as the companion app.
"""

from __future__ import annotations

import random
from typing import Protocol

from alpha_mini_wifi_encoder.constants import MAX_RANDOM_ID


class RandomIdProvider(Protocol):
    """Strategy supplying the random id shared by all chunks of a message."""

    def next_id(self) -> int:
        """Return an integer in ``[0, MAX_RANDOM_ID]``.

        Returns:
            int: An identifier within the inclusive permitted range.
        """
        ...


class FixedRandomId:
    """Always returns the same id; useful for reproducible output."""

    def __init__(self, value: int = 0x1234) -> None:
        """Create the provider.

        Args:
            value (int): Id to return.

        Returns:
            None: This function does not return a value.

        Raises:
            ValueError: If ``value`` is outside ``[0, MAX_RANDOM_ID]``.
        """
        if not 0 <= value <= MAX_RANDOM_ID:
            msg = f"random id must be in [0, {MAX_RANDOM_ID}]"
            raise ValueError(msg)
        self._value = value

    def next_id(self) -> int:
        """Return the configured id.

        Returns:
            int: The fixed identifier supplied at construction time.
        """
        return self._value


class SystemRandomId:
    """Draws ids from the operating system's entropy source, like the app."""

    def __init__(self) -> None:
        """Create the provider.

        Returns:
            None: This function does not return a value.
        """
        self._rng = random.SystemRandom()

    def next_id(self) -> int:
        """Return a fresh random id.

        Returns:
            int: An identifier drawn from the system entropy source.
        """
        return self._rng.randint(0, MAX_RANDOM_ID)
