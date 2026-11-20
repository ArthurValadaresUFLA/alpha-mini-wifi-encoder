"""Arrow-key terminal menus (``questionary``) behind a small, testable interface."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import questionary

from alpha_mini_wifi_encoder.errors import BleError, EncoderError, UserAbortedError
from alpha_mini_wifi_encoder.network import NetworkDetectionError, Security

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from alpha_mini_wifi_encoder.ble import BleDevice
    from alpha_mini_wifi_encoder.network import WifiDetector
    from alpha_mini_wifi_encoder.saved_networks import SavedNetworkProvider


class Prompter(Protocol):
    """Interactive prompts (Adapter over a prompt library)."""

    def select[T](self, message: str, choices: Sequence[tuple[str, T]]) -> T:
        """Let the user pick one of ``choices`` (label, value) with the arrow keys."""
        ...

    def text(self, message: str) -> str:
        """Ask for a line of text."""
        ...

    def password(self, message: str) -> str:
        """Ask for a hidden line of text (never echoed, never stored)."""
        ...

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Ask a yes/no question."""
        ...


class QuestionaryPrompter:
    """:class:`Prompter` implemented with ``questionary`` (up/down arrows, Enter)."""

    def __init__(self) -> None:
        """Create the prompter.

        Raises:
            EncoderError: If stdin/stdout is not an interactive terminal.
        """
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            msg = "interactive mode needs a terminal; pass the options explicitly instead"
            raise EncoderError(msg)

    def select[T](self, message: str, choices: Sequence[tuple[str, T]]) -> T:
        """Show an arrow-key menu; Ctrl-C raises :class:`UserAbortedError`."""
        items = [
            questionary.Choice(title=label, value=index) for index, (label, _) in enumerate(choices)
        ]
        answer = questionary.select(message, choices=items, use_arrow_keys=True).ask()
        if answer is None:
            raise UserAbortedError
        index: int = answer
        return choices[index][1]

    def text(self, message: str) -> str:
        """Ask for text; Ctrl-C raises :class:`UserAbortedError`."""
        answer = questionary.text(message).ask()
        if answer is None:
            raise UserAbortedError
        return str(answer).strip()

    def password(self, message: str) -> str:
        """Ask for a hidden value; Ctrl-C raises :class:`UserAbortedError`."""
        answer = questionary.password(message).ask()
        if answer is None:
            raise UserAbortedError
        return str(answer)

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Ask yes/no; Ctrl-C raises :class:`UserAbortedError`."""
        answer = questionary.confirm(message, default=default).ask()
        if answer is None:
            raise UserAbortedError
        return bool(answer)


@dataclass(frozen=True, slots=True)
class NetworkChoice:
    """Network picked in the menu (the password is asked separately).

    Attributes:
        ssid: Network name.
        security: Security mode.
        bssid: Access-point address, when known.
    """

    ssid: str
    security: Security
    bssid: str | None = None


_SECURITY_MENU: tuple[tuple[str, Security], ...] = (
    ("WPA2 (most common)", Security.WPA2),
    ("WPA", Security.WPA),
    ("WEP", Security.WEP),
    ("Open (no password)", Security.OPEN),
)
_OTHER = object()
_RESCAN = object()
_CANCEL = object()


def select_device(
    prompter: Prompter,
    scan: Callable[[], list[BleDevice]],
) -> BleDevice:
    """Scan and let the user pick a device; offers a rescan option.

    Args:
        prompter: Prompt implementation.
        scan: Callable performing a scan and returning the devices.

    Returns:
        The selected device.

    Raises:
        UserAbortedError: If the user cancels.
    """
    while True:
        devices = scan()
        choices: list[tuple[str, object]] = [(d.label, d) for d in devices]
        choices.append(("Rescan", _RESCAN))
        choices.append(("Cancel", _CANCEL))
        title = "Select the robot (up/down, Enter)" if devices else "No devices found"
        picked = prompter.select(title, choices)
        if picked is _CANCEL:
            raise UserAbortedError
        if picked is not _RESCAN:
            return _as_device(picked)


def _as_device(value: object) -> BleDevice:
    from alpha_mini_wifi_encoder.ble import BleDevice  # noqa: PLC0415

    if not isinstance(value, BleDevice):  # pragma: no cover - guarded by menu construction
        raise BleError("invalid device selection")  # noqa: EM101, TRY003
    return value


def select_security(prompter: Prompter) -> Security:
    """Ask for the security mode of a network."""
    return prompter.select("Security of the network", _SECURITY_MENU)


def select_network(
    prompter: Prompter,
    detector: WifiDetector | None,
    saved: SavedNetworkProvider | None,
) -> NetworkChoice:
    """Menu with the current network, saved networks and a manual entry option.

    Args:
        prompter: Prompt implementation.
        detector: Detects the network this machine is connected to (optional).
        saved: Lists networks already configured on this machine (optional).

    Returns:
        The chosen network. Passwords are not handled here and never stored.
    """
    choices: list[tuple[str, object]] = []
    current_ssid: str | None = None
    try:
        current = detector.detect() if detector is not None else None
    except NetworkDetectionError:
        current = None
    if current is not None:
        current_ssid = current.ssid
        choices.append((f"Current network: {current.ssid} ({current.security.name})", current))
    try:
        configured = saved.list_networks() if saved is not None else []
    except NetworkDetectionError:
        configured = []
    for network in configured:
        if network.ssid != current_ssid:
            label = (
                f"{network.ssid} ({network.security.name})" if network.security else network.ssid
            )
            choices.append((label, network))
    choices.append(("Enter another network...", _OTHER))

    picked = prompter.select("Select the Wi-Fi network for the robot", choices)
    if picked is _OTHER:
        ssid = prompter.text("Network name (SSID)")
        return NetworkChoice(ssid, select_security(prompter))
    ssid = getattr(picked, "ssid", "")
    security: Security | None = getattr(picked, "security", None)
    return NetworkChoice(
        ssid,
        security if security is not None else select_security(prompter),
        getattr(picked, "bssid", None),
    )


def ask_network_password(prompter: Prompter, choice: NetworkChoice) -> str:
    """Ask for the network password (hidden); open networks need none.

    Args:
        prompter: Prompt implementation.
        choice: The selected network.

    Returns:
        The password, or ``""`` for open networks. It is kept in memory only.
    """
    if choice.security is Security.OPEN:
        return ""
    return prompter.password(f"Password for '{choice.ssid}' (not stored)")
