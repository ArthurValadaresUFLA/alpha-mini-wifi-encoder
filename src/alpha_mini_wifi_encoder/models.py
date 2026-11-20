"""Validated value objects describing a Wi-Fi provisioning request."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alpha_mini_wifi_encoder.constants import (
    MAX_PASSWORD_BYTES,
    MAX_SSID_BYTES,
    WifiCommand,
)

_BSSID_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

DontPing = Literal["0", "1"]
"""Wire representation of the "do not ping the network" flag."""


class WifiCredentials(BaseModel):
    """Wi-Fi network description supplied by the user.

    Attributes:
        ssid: Network name (1 to 32 UTF-8 bytes).
        capabilities: Capability string as reported by a Wi-Fi scan, e.g.
            ``"[WPA2-PSK-CCMP][ESS]"``.
        password: Network password; an empty string denotes an open network.
        bssid: Optional access-point MAC address (``aa:bb:cc:dd:ee:ff``).
        dont_ping: Optional flag; ``None`` omits it from the payload, as the
            native app flows do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ssid: str = Field(min_length=1)
    capabilities: str
    password: str = ""
    bssid: str | None = None
    dont_ping: DontPing | None = None

    @field_validator("ssid")
    @classmethod
    def _check_ssid_size(cls, value: str) -> str:
        if len(value.encode()) > MAX_SSID_BYTES:
            msg = f"SSID exceeds {MAX_SSID_BYTES} UTF-8 bytes"
            raise ValueError(msg)
        return value

    @field_validator("password")
    @classmethod
    def _check_password_size(cls, value: str) -> str:
        if len(value.encode()) > MAX_PASSWORD_BYTES:
            msg = f"password exceeds {MAX_PASSWORD_BYTES} UTF-8 bytes"
            raise ValueError(msg)
        return value

    @field_validator("bssid")
    @classmethod
    def _check_bssid_format(cls, value: str | None) -> str | None:
        if value is not None and not _BSSID_PATTERN.fullmatch(value):
            msg = "BSSID must look like aa:bb:cc:dd:ee:ff"
            raise ValueError(msg)
        return value


class WifiPayload(BaseModel):
    """Inner JSON object (``BindOrSwitchWifiRequest.data``).

    Gson serialises fields in class-declaration order (subclass first, then
    superclass) and omits nulls; the declaration order below, together with
    ``exclude_none``, reproduces that behaviour: ``b, c, p, s, v`` then ``co``.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bssid: str | None = Field(default=None, serialization_alias="b")
    capabilities: str = Field(serialization_alias="c")
    password: str = Field(serialization_alias="p")
    ssid: str = Field(serialization_alias="s")
    dont_ping: DontPing | None = Field(default=None, serialization_alias="v")
    command: WifiCommand = Field(serialization_alias="co")

    @classmethod
    def from_credentials(cls, credentials: WifiCredentials, command: WifiCommand) -> Self:
        """Build the wire payload for ``credentials`` and ``command``.

        Args:
            credentials: Validated network description.
            command: ``co`` value to embed.

        Returns:
            The immutable payload ready to be serialised.
        """
        return cls(
            bssid=credentials.bssid,
            capabilities=credentials.capabilities,
            password=credentials.password,
            ssid=credentials.ssid,
            dont_ping=credentials.dont_ping,
            command=command,
        )

    def to_wire_dict(self) -> dict[str, str | int]:
        """Return the payload keyed by wire names, without ``None`` values."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
