"""Validated value objects describing a Wi-Fi provisioning request.

:class:`WifiCredentials` holds the user-supplied network description and
enforces IEEE 802.11 size and BSSID format constraints. :class:`WifiPayload`
maps those credentials onto the short field names and declaration order
expected by the companion app's Gson serialisation. The :data:`DontPing` type
alias describes the wire representation of the optional ping-suppression flag.
"""

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
"""re.Pattern[str]: Compiled validator for colon-separated MAC addresses."""

DontPing = Literal["0", "1"]
"""Literal["0", "1"]: Wire representation of the "do not ping the network" flag."""


class WifiCredentials(BaseModel):
    """Wi-Fi network description supplied by the user.

    Attributes:
        ssid (str): Network name (1 to 32 UTF-8 bytes).
        capabilities (str): Capability string as reported by a Wi-Fi scan, e.g.
            ``"[WPA2-PSK-CCMP][ESS]"``.
        password (str): Network password; an empty string denotes an open network.
        bssid (str | None): Optional access-point MAC address (``aa:bb:cc:dd:ee:ff``).
        dont_ping (DontPing | None): Optional flag; ``None`` omits it from the payload, as the
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
        """Validate that the SSID fits the IEEE 802.11 byte limit.

        Args:
            value (str): SSID to validate.

        Returns:
            str: The unchanged SSID.

        Raises:
            ValueError: If its UTF-8 encoding exceeds ``MAX_SSID_BYTES``.
        """
        if len(value.encode()) > MAX_SSID_BYTES:
            msg = f"SSID exceeds {MAX_SSID_BYTES} UTF-8 bytes"
            raise ValueError(msg)
        return value

    @field_validator("password")
    @classmethod
    def _check_password_size(cls, value: str) -> str:
        """Validate that the password fits the wire-format byte limit.

        Args:
            value (str): Password to validate.

        Returns:
            str: The unchanged password.

        Raises:
            ValueError: If its UTF-8 encoding exceeds ``MAX_PASSWORD_BYTES``.
        """
        if len(value.encode()) > MAX_PASSWORD_BYTES:
            msg = f"password exceeds {MAX_PASSWORD_BYTES} UTF-8 bytes"
            raise ValueError(msg)
        return value

    @field_validator("bssid")
    @classmethod
    def _check_bssid_format(cls, value: str | None) -> str | None:
        """Validate the optional BSSID against the MAC address format.

        Args:
            value (str | None): BSSID to validate, if supplied.

        Returns:
            str | None: The unchanged BSSID.

        Raises:
            ValueError: If ``value`` is not ``None`` and is not a colon-separated MAC address.
        """
        if value is not None and not _BSSID_PATTERN.fullmatch(value):
            msg = "BSSID must look like aa:bb:cc:dd:ee:ff"
            raise ValueError(msg)
        return value


class WifiPayload(BaseModel):
    """Inner JSON object (``BindOrSwitchWifiRequest.data``).

    Gson serialises fields in class-declaration order (subclass first, then
    superclass) and omits nulls; the declaration order below, together with
    ``exclude_none``, reproduces that behaviour: ``b, c, p, s, v`` then ``co``.

    Attributes:
        bssid (str | None): Optional access-point MAC address, serialised as ``b``.
        capabilities (str): Scan capability string, serialised as ``c``.
        password (str): Network password, serialised as ``p``.
        ssid (str): Network name, serialised as ``s``.
        dont_ping (DontPing | None): Optional ping flag, serialised as ``v``.
        command (WifiCommand): Provisioning command, serialised as ``co``.
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
            credentials (WifiCredentials): Validated network description.
            command (WifiCommand): ``co`` value to embed.

        Returns:
            Self: The immutable payload ready to be serialised.
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
        """Return the payload keyed by wire names, without ``None`` values.

        Returns:
            dict[str, str | int]: Field values keyed by their short Gson names.
        """
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
