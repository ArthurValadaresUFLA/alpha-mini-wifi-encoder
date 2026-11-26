"""Central stop conditions for the programming client.

:class:`StopGuard` enforces, in one place, the criteria that must halt a session:

* only commands from an explicit allow-list may be sent, which excludes every reset, erase,
  unbind or factory opcode (their ids were never recovered, so an allow-list is the only
  enforceable form);
* a LAN handshake that is refused, carries a non-zero ``errorCode`` or reports an unexpected
  interface version stops the session.

Detecting a server-signed token is not possible from the bytes alone; the client simply never
builds or forwards such material.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha_mini_wifi_encoder.constants import LAN_HANDSHAKE_REQUEST_COMMAND, CodeMaoCommand
from alpha_mini_wifi_encoder.errors import EncoderError

if TYPE_CHECKING:
    from alpha_mini_wifi_encoder.codemao import CodeMaoRequest, LanHandshakeResult

DEFAULT_ALLOWED_COMMANDS: frozenset[int] = frozenset(
    {*CodeMaoCommand, LAN_HANDSHAKE_REQUEST_COMMAND},
)
"""frozenset[int]: Command ids this client is allowed to send."""


class StopConditionError(EncoderError):
    """Raised when a stop criterion fires; the session must not continue."""


class StopGuard:
    """Evaluate the stop criteria for outgoing requests and for the LAN handshake."""

    def __init__(
        self,
        *,
        allowed_commands: frozenset[int] = DEFAULT_ALLOWED_COMMANDS,
        expected_interface_version: int | None = None,
    ) -> None:
        """Create the guard.

        Args:
            allowed_commands (frozenset[int]): Command ids that may be sent.
            expected_interface_version (int | None): ``btInterfaceVersion`` the robot must
                report; ``None`` accepts any version.

        Returns:
            None: This method does not return a value.
        """
        self._allowed = allowed_commands
        self._expected_version = expected_interface_version

    def check_outgoing(self, request: CodeMaoRequest) -> None:
        """Refuse any command outside the allow-list.

        Args:
            request (CodeMaoRequest): Request about to be sent.

        Returns:
            None: This method does not return a value.

        Raises:
            StopConditionError: If the command id is not allowed.
        """
        if request.command_id not in self._allowed:
            msg = f"command {request.command_id} is not in the allow-list; refusing to send it"
            raise StopConditionError(msg)

    def check_handshake(self, result: LanHandshakeResult) -> None:
        """Stop on a refused handshake, a non-zero error code or an unexpected version.

        Args:
            result (LanHandshakeResult): Decoded handshake response.

        Returns:
            None: This method does not return a value.

        Raises:
            StopConditionError: If any stop criterion is met.
        """
        if not result.is_success or result.error_code != 0:
            msg = (
                f"handshake refused: resultCode={result.result_code}, "
                f"errorCode={result.error_code}, firmware={result.android_firmware_version!r}"
            )
            raise StopConditionError(msg)
        if (
            self._expected_version is not None
            and result.bt_interface_version != self._expected_version
        ):
            msg = (
                f"incompatible robot interface version {result.bt_interface_version} "
                f"(expected {self._expected_version})"
            )
            raise StopConditionError(msg)
