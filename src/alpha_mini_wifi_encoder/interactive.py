"""Arrow-key terminal menus (``questionary``) behind a small, testable interface.

Terminal interaction is isolated behind the :class:`Prompter` protocol so that callers can be
tested with a fake prompter. :class:`QuestionaryPrompter` is the ``questionary`` implementation,
:class:`NetworkChoice` is the selected-network value type, and :func:`select_device`,
:func:`select_security`, :func:`select_network` and :func:`ask_network_password` compose the
interactive workflow.
"""

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
    """Protocol for interactive prompts, acting as an adapter over a prompt library."""

    def select[T](self, message: str, choices: Sequence[tuple[str, T]]) -> T:
        """Let the user pick one of ``choices`` using the arrow keys.

        Args:
            message (str): Prompt text shown above the choices.
            choices (Sequence[tuple[str, T]]): Labelled values offered to the user.

        Returns:
            T: Value associated with the selected label.

        Raises:
            UserAbortedError: If the user cancels the prompt.
        """
        ...

    def text(self, message: str) -> str:
        """Ask for a line of text.

        Args:
            message (str): Prompt text shown to the user.

        Returns:
            str: The entered line, stripped of surrounding whitespace.

        Raises:
            UserAbortedError: If the user cancels the prompt.
        """
        ...

    def password(self, message: str) -> str:
        """Ask for a hidden line of text that is never echoed or stored.

        Args:
            message (str): Prompt text shown to the user.

        Returns:
            str: The entered secret, without surrounding whitespace being stripped.

        Raises:
            UserAbortedError: If the user cancels the prompt.
        """
        ...

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Ask a yes/no question.

        Args:
            message (str): Prompt text shown to the user.
            default (bool): Answer selected when the user presses Enter without a choice.

        Returns:
            bool: True for a yes answer, False for a no answer.

        Raises:
            UserAbortedError: If the user cancels the prompt.
        """
        ...


class QuestionaryPrompter:
    """Concrete :class:`Prompter` implemented with ``questionary`` (up/down arrows, Enter)."""

    def __init__(self) -> None:
        """Create the prompter.

        Returns:
            None: This method does not return a value.

        Raises:
            EncoderError: If stdin/stdout is not an interactive terminal.
        """
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            msg = "interactive mode needs a terminal; pass the options explicitly instead"
            raise EncoderError(msg)

    def select[T](self, message: str, choices: Sequence[tuple[str, T]]) -> T:
        """Show an arrow-key menu and return the selected value.

        Args:
            message (str): Prompt text shown above the choices.
            choices (Sequence[tuple[str, T]]): Labelled values offered to the user.

        Returns:
            T: Value associated with the selected label.

        Raises:
            UserAbortedError: If the user cancels the prompt, for example with Ctrl-C.
        """
        items = [
            questionary.Choice(title=label, value=index) for index, (label, _) in enumerate(choices)
        ]
        answer = questionary.select(message, choices=items, use_arrow_keys=True).ask()
        if answer is None:
            raise UserAbortedError
        index: int = answer
        return choices[index][1]

    def text(self, message: str) -> str:
        """Ask for text and return the stripped answer.

        Args:
            message (str): Prompt text shown to the user.

        Returns:
            str: The entered line, stripped of surrounding whitespace.

        Raises:
            UserAbortedError: If the user cancels the prompt, for example with Ctrl-C.
        """
        answer = questionary.text(message).ask()
        if answer is None:
            raise UserAbortedError
        return str(answer).strip()

    def password(self, message: str) -> str:
        """Ask for a hidden value and return it verbatim.

        Args:
            message (str): Prompt text shown to the user.

        Returns:
            str: The entered secret, with no whitespace stripping applied.

        Raises:
            UserAbortedError: If the user cancels the prompt, for example with Ctrl-C.
        """
        answer = questionary.password(message).ask()
        if answer is None:
            raise UserAbortedError
        return str(answer)

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Ask a yes/no question and return the user's answer.

        Args:
            message (str): Prompt text shown to the user.
            default (bool): Answer selected when the user presses Enter without a choice.

        Returns:
            bool: True for a yes answer, False for a no answer.

        Raises:
            UserAbortedError: If the user cancels the prompt, for example with Ctrl-C.
        """
        answer = questionary.confirm(message, default=default).ask()
        if answer is None:
            raise UserAbortedError
        return bool(answer)


@dataclass(frozen=True, slots=True)
class NetworkChoice:
    """Network picked in the menu; the password is asked for separately.

    Attributes:
        ssid (str): Network name.
        security (Security): Security mode.
        bssid (str | None): Access-point address, when known.
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
"""tuple[tuple[str, Security], ...]: Labels and values offered by the security menu."""
_OTHER = object()
"""object: Sentinel value for the manual network entry option."""
_RESCAN = object()
"""object: Sentinel value for the rescan option."""
_CANCEL = object()
"""object: Sentinel value for the cancel option."""


def select_device(
    prompter: Prompter,
    scan: Callable[[], list[BleDevice]],
) -> BleDevice:
    """Scan for devices and let the user pick one, offering a rescan option.

    Args:
        prompter (Prompter): Prompt implementation.
        scan (Callable[[], list[BleDevice]]): Callable performing a scan and returning the devices.

    Returns:
        BleDevice: The selected device.

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
    """Validate and return a device selected from the menu.

    Args:
        value (object): Value returned by the device selection menu.

    Returns:
        BleDevice: The validated device.

    Raises:
        BleError: If ``value`` is not a :class:`BleDevice`.
    """
    from alpha_mini_wifi_encoder.ble import BleDevice  # noqa: PLC0415

    if not isinstance(value, BleDevice):  # pragma: no cover - guarded by menu construction
        raise BleError("invalid device selection")  # noqa: EM101, TRY003
    return value


def select_security(prompter: Prompter) -> Security:
    """Ask for the security mode of a network.

    Args:
        prompter (Prompter): Prompt implementation.

    Returns:
        Security: The selected security mode.

    Raises:
        UserAbortedError: If the user cancels the prompt.
    """
    return prompter.select("Security of the network", _SECURITY_MENU)


def select_network(
    prompter: Prompter,
    detector: WifiDetector | None,
    saved: SavedNetworkProvider | None,
) -> NetworkChoice:
    """Show a menu with the current, saved and manually entered networks.

    Args:
        prompter (Prompter): Prompt implementation.
        detector (WifiDetector | None): Detects the network this machine is connected to, if any.
        saved (SavedNetworkProvider | None): Lists networks already configured on this machine.

    Returns:
        NetworkChoice: The chosen network. Passwords are not handled here and never stored.

    Raises:
        UserAbortedError: If the user cancels the prompt.
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
    """Ask for the network password with the input hidden; open networks need none.

    Args:
        prompter (Prompter): Prompt implementation.
        choice (NetworkChoice): The selected network.

    Returns:
        str: The password, or ``""`` for open networks. It is kept in memory only.

    Raises:
        UserAbortedError: If the user cancels the prompt.
    """
    if choice.security is Security.OPEN:
        return ""
    return prompter.password(f"Password for '{choice.ssid}' (not stored)")
