"""Wi-Fi networks already configured (saved) on this machine.

Only names and, where cheaply available, the security mode are read. Passwords
are never read from the operating system.

The module exports the immutable `SavedNetwork` value object, the
`SavedNetworkProvider` strategy interface and the `create_saved_networks`
factory together with its Linux, Windows and macOS implementations
(`NmcliSavedNetworks`, `NetshSavedNetworks` and `NetworksetupSavedNetworks`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from alpha_mini_wifi_encoder.network import (
    WINDOWS_AUTH_VALUE,
    NetworkDetectionError,
    Security,
    UnsupportedSecurityError,
    classify_security,
    run_command,
    split_terse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from alpha_mini_wifi_encoder.network import CommandRunner

_NM_WIFI_TYPES = frozenset({"802-11-wireless", "wifi"})
"""frozenset[str]: NetworkManager connection types treated as Wi-Fi profiles."""

_NM_KEY_MGMT: dict[str, Security | None] = {
    "": Security.OPEN,
    "none": Security.WEP,
    "wpa-psk": Security.WPA2,
    "sae": None,
}
"""dict[str, Security | None]: Maps key-management values to security modes."""

_NM_SKIPPED_KEY_MGMT = frozenset({"wpa-eap", "wpa-eap-suite-b-192", "ieee8021x"})
"""frozenset[str]: Key-management values identifying enterprise profiles."""


@dataclass(frozen=True, slots=True)
class SavedNetwork:
    """A network configured on this machine.

    Attributes:
        ssid (str): Network name.
        security (Security | None): Security mode, or `None` when it could not be
            determined.
    """

    ssid: str
    security: Security | None = None


class SavedNetworkProvider(Protocol):
    """Strategy listing the Wi-Fi networks configured on this machine."""

    def list_networks(self) -> list[SavedNetwork]:
        """Return the saved networks, sorted by name, without duplicates.

        Returns:
            list[SavedNetwork]: Saved networks ordered case-insensitively by SSID.

        Raises:
            NetworkDetectionError: If the operating system query fails.
        """
        ...


def _unique_sorted(networks: list[SavedNetwork]) -> list[SavedNetwork]:
    """Return the saved networks deduplicated by SSID and sorted case-insensitively.

    Args:
        networks (list[SavedNetwork]): Networks to filter and order.

    Returns:
        list[SavedNetwork]: Non-empty networks in alphabetical SSID order.
    """
    unique = {network.ssid: network for network in networks if network.ssid}
    return sorted(unique.values(), key=lambda n: n.ssid.lower())


class NmcliSavedNetworks:
    """Linux provider based on NetworkManager connection profiles."""

    def __init__(self, runner: CommandRunner = run_command) -> None:
        """Create the provider.

        Args:
            runner (CommandRunner): Command executor (injectable for tests).

        Returns:
            None: This function does not return a value.
        """
        self._runner = runner

    def list_networks(self) -> list[SavedNetwork]:
        """List Wi-Fi profiles (enterprise profiles are skipped).

        Returns:
            list[SavedNetwork]: Saved Wi-Fi networks sorted by SSID.

        Raises:
            NetworkDetectionError: If the `nmcli` listing command fails.
        """
        listing = self._runner(
            ("nmcli", "-t", "-e", "yes", "-f", "NAME,TYPE", "connection", "show")
        )
        networks: list[SavedNetwork] = []
        for line in listing.splitlines():
            fields = split_terse(line)
            if len(fields) != 2 or fields[1] not in _NM_WIFI_TYPES:  # noqa: PLR2004
                continue
            network = self._profile(fields[0])
            if network is not None:
                networks.append(network)
        return _unique_sorted(networks)

    def _profile(self, name: str) -> SavedNetwork | None:
        """Return the saved network described by a NetworkManager profile.

        Args:
            name (str): Connection profile name reported by `nmcli`.

        Returns:
            SavedNetwork | None: The network and its security mode, or `None` if
                the profile is unavailable or uses enterprise key management.
        """
        fields = "802-11-wireless.ssid,802-11-wireless-security.key-mgmt"
        try:
            output = self._runner(
                ("nmcli", "-t", "-e", "yes", "-g", fields, "connection", "show", "id", name)
            )
        except NetworkDetectionError:
            return None
        values = [":".join(split_terse(v)) for v in output.split("\n")] + ["", ""]
        ssid, key_mgmt = values[0] or name, values[1]
        if key_mgmt in _NM_SKIPPED_KEY_MGMT:
            return None
        return SavedNetwork(ssid, _NM_KEY_MGMT.get(key_mgmt))


class NetshSavedNetworks:
    """Windows provider based on `netsh wlan show profiles`."""

    def __init__(self, runner: CommandRunner = run_command) -> None:
        """Create the provider.

        Args:
            runner (CommandRunner): Command executor (injectable for tests).

        Returns:
            None: This function does not return a value.
        """
        self._runner = runner

    def list_networks(self) -> list[SavedNetwork]:
        """List profiles; security comes from each profile's authentication value.

        Returns:
            list[SavedNetwork]: Saved profiles ordered case-insensitively by SSID.

        Raises:
            NetworkDetectionError: If the `netsh` listing command fails.
        """
        networks: list[SavedNetwork] = []
        for line in self._runner(("netsh", "wlan", "show", "profiles")).splitlines():
            key, _, value = line.partition(":")
            if re.search(r"profile|perfil", key, re.IGNORECASE) and value.strip():
                name = value.strip()
                networks.append(SavedNetwork(name, self._security(name)))
        return _unique_sorted(networks)

    def _security(self, name: str) -> Security | None:
        """Return the security mode declared by a saved Windows profile.

        Args:
            name (str): Profile name reported by `netsh`.

        Returns:
            Security | None: The mapped security mode, or `None` if the profile
                cannot be read or its authentication value is unsupported.
        """
        try:
            output = self._runner(("netsh", "wlan", "show", "profile", f"name={name}"))
        except NetworkDetectionError:
            return None
        for line in output.splitlines():
            value = line.partition(":")[2].strip()
            if WINDOWS_AUTH_VALUE.fullmatch(value.upper()):
                try:
                    return classify_security(value)
                except UnsupportedSecurityError:
                    return None
        return None


class NetworksetupSavedNetworks:
    """macOS provider based on `networksetup -listpreferredwirelessnetworks`."""

    def __init__(self, runner: CommandRunner = run_command, interface: str = "en0") -> None:
        """Create the provider.

        Args:
            runner (CommandRunner): Command executor (injectable for tests).
            interface (str): Wi-Fi network interface name.

        Returns:
            None: This function does not return a value.
        """
        self._runner = runner
        self._interface = interface

    def list_networks(self) -> list[SavedNetwork]:
        """List preferred networks (security is unknown on macOS).

        Returns:
            list[SavedNetwork]: Preferred networks ordered case-insensitively by SSID.

        Raises:
            NetworkDetectionError: If the `networksetup` command fails.
        """
        output = self._runner(("networksetup", "-listpreferredwirelessnetworks", self._interface))
        names = [line.strip() for line in output.splitlines()[1:] if line.strip()]
        return _unique_sorted([SavedNetwork(name) for name in names])


def create_saved_networks(
    system: str | None = None,
    runner: CommandRunner = run_command,
) -> SavedNetworkProvider:
    """Create the saved-network provider for an operating system (factory).

    Args:
        system (str | None): `platform.system()` value; defaults to the running OS.
        runner (CommandRunner): Command executor shared with the provider.

    Returns:
        SavedNetworkProvider: The provider for the requested platform.

    Raises:
        NetworkDetectionError: If the operating system is not supported.
    """
    import platform  # noqa: PLC0415 - keep module import cheap

    name = system or platform.system()
    factories: dict[str, Callable[[CommandRunner], SavedNetworkProvider]] = {
        "Linux": NmcliSavedNetworks,
        "Windows": NetshSavedNetworks,
        "Darwin": NetworksetupSavedNetworks,
    }
    try:
        return factories[name](runner)
    except KeyError as error:
        msg = f"saved-network listing is not supported on {name!r}"
        raise NetworkDetectionError(msg) from error
