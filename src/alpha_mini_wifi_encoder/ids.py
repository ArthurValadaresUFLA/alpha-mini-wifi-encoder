"""Providers for the per-message random identifier of GATT chunks."""

from __future__ import annotations

import random
from typing import Protocol

from alpha_mini_wifi_encoder.constants import MAX_RANDOM_ID


class RandomIdProvider(Protocol):
    """Strategy supplying the random id shared by all chunks of a message."""

    def next_id(self) -> int:
        """Return an integer in ``[0, MAX_RANDOM_ID]``."""
        ...


class FixedRandomId:
    """Always returns the same id; useful for reproducible output."""

    def __init__(self, value: int = 0x1234) -> None:
        """Create the provider.

        Args:
            value: Id to return.

        Raises:
            ValueError: If ``value`` is outside ``[0, MAX_RANDOM_ID]``.
        """
        if not 0 <= value <= MAX_RANDOM_ID:
            msg = f"random id must be in [0, {MAX_RANDOM_ID}]"
            raise ValueError(msg)
        self._value = value

    def next_id(self) -> int:
        """Return the configured id."""
        return self._value


class SystemRandomId:
    """Draws ids from the operating system's entropy source, like the app."""

    def __init__(self) -> None:
        """Create the provider."""
        self._rng = random.SystemRandom()

    def next_id(self) -> int:
        """Return a fresh random id."""
        return self._rng.randint(0, MAX_RANDOM_ID)
