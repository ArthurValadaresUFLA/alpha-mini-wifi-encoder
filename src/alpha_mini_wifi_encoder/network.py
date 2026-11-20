"""Detection of the Wi-Fi network the current machine is connected to.

Only the network *description* (SSID, security, BSSID) is read from the
operating system. The password is deliberately never read from OS keystores:
it must be supplied by the user.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from alpha_mini_wifi_encoder.errors import EncoderError
from alpha_mini_wifi_encoder.models import DontPing, WifiCredentials

CommandRunner = Callable[[Sequence[str]], str]
"""Callable that executes a command and returns its standard output."""

_COMMAND_TIMEOUT_SECONDS = 10
_SECURITY_TOKENS = re.compile(r"WPA3|WPA2|WPA1|WPA|WEP|ENTERPRISE|802\.1X|NONE|OPEN|ABERTA|--")
_WINDOWS_AUTH_VALUE = re.compile(r"^(?:OPEN|ABERTA|WEP|WPA\d?(?:-.+)?)$")
_REDACTED = "<redacted>"


class NetworkDetectionError(EncoderError):
    """Raised when the current Wi-Fi network cannot be determined."""


class UnsupportedSecurityError(NetworkDetectionError):
    """Raised when the network uses a security mode the app flow cannot express."""


class Security(StrEnum):
    """Capability strings used by the app for the phone's *current* Wi-Fi.

    The value ``N`` for open networks is taken from the analysis report's list of
    ``WifiStatusHelper`` values and is not confirmed on hardware.
    """

    OPEN = "N"
    WEP = "WEP"
    WPA = "WPA"
    WPA2 = "WPA2"


@dataclass(frozen=True, slots=True)
class DetectedNetwork:
    """Wi-Fi network detected on the local machine.

    Attributes:
        ssid: Network name.
        security: Security mode mapped to the app's capability vocabulary.
        bssid: Access-point MAC address, when the OS exposes it.
    """

    ssid: str
    security: Security
    bssid: str | None = None

    def to_credentials(
        self,
        *,
        password: str = "",
        include_bssid: bool = False,
        dont_ping: DontPing | None = None,
    ) -> WifiCredentials:
        """Convert to validated credentials.

        Args:
            password: Network password (empty for open networks).
            include_bssid: Whether to send the BSSID; the native app flow for the
                phone's current network omits it.
            dont_ping: Optional ``v`` flag.

        Returns:
            Credentials ready for :class:`~.encoder.WifiProvisioningEncoder`.
        """
        return WifiCredentials(
            ssid=self.ssid,
            capabilities=self.security.value,
            password=password,
            bssid=self.bssid if include_bssid else None,
            dont_ping=dont_ping,
        )


def classify_security(raw: str) -> Security:
    """Map an OS-specific security description to :class:`Security`.

    Args:
        raw: Text such as ``"WPA2"``, ``"WPA2-Personal"`` or
            ``"spairport_security_mode_wpa2_personal"``.

    Returns:
        The matching security mode (WPA2 wins in WPA2/WPA3 transition mode).

    Raises:
        UnsupportedSecurityError: For enterprise, WPA3-only or unknown modes.
    """
    tokens = set(_SECURITY_TOKENS.findall(raw.upper().replace("_", " ")))
    result: Security | None = None
    if tokens & {"ENTERPRISE", "802.1X"}:
        result = None
    elif "WPA2" in tokens:
        result = Security.WPA2
    elif tokens & {"WPA", "WPA1"}:
        result = Security.WPA
    elif "WEP" in tokens:
        result = Security.WEP
    elif tokens & {"NONE", "OPEN", "ABERTA", "--"} and "WPA3" not in tokens:
        result = Security.OPEN
    if result is None:
        msg = f"unsupported or unknown Wi-Fi security: {raw!r}"
        raise UnsupportedSecurityError(msg)
    return result


def run_command(args: Sequence[str]) -> str:
    """Run ``args`` without a shell and return its standard output.

    Raises:
        NetworkDetectionError: If the command is missing, fails or times out.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - no shell, fixed arguments
            list(args),
            capture_output=True,
            text=True,
            check=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        msg = f"command not found: {args[0]}"
        raise NetworkDetectionError(msg) from error
    except subprocess.CalledProcessError as error:
        msg = f"{args[0]} failed: {error.stderr.strip() or error.returncode}"
        raise NetworkDetectionError(msg) from error
    except subprocess.TimeoutExpired as error:
        msg = f"{args[0]} timed out"
        raise NetworkDetectionError(msg) from error
    return completed.stdout


class WifiDetector(Protocol):
    """Strategy that reports the network the machine is connected to."""

    def detect(self) -> DetectedNetwork:
        """Return the current network.

        Raises:
            NetworkDetectionError: If the machine is not connected or the
                output cannot be understood.
        """
        ...


def _not_connected() -> NetworkDetectionError:
    return NetworkDetectionError("this machine is not connected to a Wi-Fi network")


def _split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line on unescaped colons and unescape the fields."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


class NmcliDetector:
    """Linux detector based on NetworkManager's ``nmcli``."""

    COMMAND = ("nmcli", "-t", "-e", "yes", "-f", "ACTIVE,SSID,BSSID,SECURITY", "dev", "wifi")

    def __init__(self, runner: CommandRunner = run_command) -> None:
        """Create the detector.

        Args:
            runner: Command executor (injectable for tests).
        """
        self._runner = runner

    def detect(self) -> DetectedNetwork:
        """Return the active Wi-Fi network reported by ``nmcli``."""
        for line in self._runner(self.COMMAND).splitlines():
            fields = _split_terse(line)
            if len(fields) == 4 and fields[0] == "yes":  # noqa: PLR2004
                _, ssid, bssid, security = fields
                if not ssid:
                    break
                return DetectedNetwork(ssid, classify_security(security), bssid.lower() or None)
        raise _not_connected()


class NetshDetector:
    """Windows detector based on ``netsh wlan show interfaces``.

    ``SSID`` and ``BSSID`` keys are language-neutral; the authentication value is
    matched by content (``Open``/``Aberta``, ``WPA2-Personal``...), so English and
    Portuguese output are both understood.
    """

    COMMAND = ("netsh", "wlan", "show", "interfaces")

    def __init__(self, runner: CommandRunner = run_command) -> None:
        """Create the detector.

        Args:
            runner: Command executor (injectable for tests).
        """
        self._runner = runner

    def detect(self) -> DetectedNetwork:
        """Return the connected network reported by ``netsh``."""
        ssid: str | None = None
        bssid: str | None = None
        auth: str | None = None
        for line in self._runner(self.COMMAND).splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip().upper(), value.strip()
            if key == "SSID":
                ssid = value
            elif key.endswith("BSSID"):
                bssid = value.lower() or None
            elif auth is None and _WINDOWS_AUTH_VALUE.fullmatch(value.upper()):
                auth = value
        if not ssid:
            raise _not_connected()
        if auth is None:
            msg = "could not determine the Wi-Fi security from netsh output"
            raise UnsupportedSecurityError(msg)
        return DetectedNetwork(ssid, classify_security(auth), bssid)


class SystemProfilerDetector:
    """Best-effort macOS detector based on ``system_profiler SPAirPortDataType``.

    Recent macOS versions redact the SSID unless the calling application has
    Location Services permission; that case is reported as an error.
    """

    COMMAND = ("system_profiler", "SPAirPortDataType", "-json")

    def __init__(self, runner: CommandRunner = run_command) -> None:
        """Create the detector.

        Args:
            runner: Command executor (injectable for tests).
        """
        self._runner = runner

    def detect(self) -> DetectedNetwork:
        """Return the current network from the profiler's JSON output."""
        try:
            document: Mapping[str, Any] = json.loads(self._runner(self.COMMAND))
            sections = document["SPAirPortDataType"]
        except (ValueError, KeyError, TypeError) as error:
            msg = "unexpected system_profiler output"
            raise NetworkDetectionError(msg) from error
        for section in sections:
            for interface in section.get("spairport_airport_interfaces", []):
                current = interface.get("spairport_current_network_information")
                if current:
                    return self._from_current(current)
        raise _not_connected()

    @staticmethod
    def _from_current(current: Mapping[str, Any]) -> DetectedNetwork:
        ssid = str(current.get("_name", ""))
        if not ssid or ssid == _REDACTED:
            msg = "macOS redacted the SSID (grant Location Services permission to the terminal)"
            raise NetworkDetectionError(msg)
        security = classify_security(str(current.get("spairport_security_mode", "")))
        return DetectedNetwork(ssid, security)


def create_detector(
    system: str | None = None,
    runner: CommandRunner = run_command,
) -> WifiDetector:
    """Return the detector for an operating system (Factory).

    Args:
        system: ``platform.system()`` value; defaults to the running OS.
        runner: Command executor shared with the detector.

    Raises:
        NetworkDetectionError: If the operating system is not supported.
    """
    name = system or platform.system()
    factories: dict[str, Callable[[CommandRunner], WifiDetector]] = {
        "Linux": NmcliDetector,
        "Windows": NetshDetector,
        "Darwin": SystemProfilerDetector,
    }
    try:
        return factories[name](runner)
    except KeyError as error:
        msg = f"current-network detection is not supported on {name!r}"
        raise NetworkDetectionError(msg) from error
