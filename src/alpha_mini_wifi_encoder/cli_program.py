"""Command-line front end for the programming (CodeMao) mode over BLE or LAN.

The module exports the :func:`program` click group (``encode`` and ``run``) and the
:func:`lan_scan` command. ``program encode`` is offline. ``program run`` and ``lan-scan``
perform I/O; they can be given test doubles through the injectable factories
:func:`create_ble_transport`, :func:`create_lan_transport`, :func:`create_lan_scanner` and
:func:`create_network_detector`.

Only the three commands recovered from the app are exposed (handshake, start/stop program and
revert origin), and :class:`~.stop_guard.StopGuard` refuses anything else. No reset, erase, unbind,
factory or ADB command exists in this client.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from alpha_mini_wifi_encoder.ble_transport import BleTransport
from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.cli_support import handle_errors
from alpha_mini_wifi_encoder.codemao import (
    CodeMaoCodec,
    CodeMaoReply,
    CodeMaoRequest,
    CommandResult,
    control_program_request,
    handshake_request,
    revert_origin_request,
)
from alpha_mini_wifi_encoder.constants import (
    GATT_CHUNK_TRAILER_SIZE,
    MAX_RANDOM_ID,
    CodeMaoCommand,
)
from alpha_mini_wifi_encoder.errors import UserAbortedError
from alpha_mini_wifi_encoder.ids import FixedRandomId
from alpha_mini_wifi_encoder.lan import LanTransport
from alpha_mini_wifi_encoder.lan_discovery import DiscoveryReport, LanScanner, ZeroconfLanScanner
from alpha_mini_wifi_encoder.network import NetworkDetectionError, WifiDetector, create_detector
from alpha_mini_wifi_encoder.session import (
    DEFAULT_REPLY_TIMEOUT_SECONDS,
    CodeMaoSession,
    FrameTransport,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class ProgramAction:
    """One action of ``program run`` / ``program encode``, defined in a single place.

    Attributes:
        name (str): Command-line name of the action.
        builder (Callable[[], CodeMaoRequest]): Builds the request shown by ``program encode``.
        runner (Callable[[CodeMaoSession], Awaitable[CommandResult]]): Performs the action.
    """

    name: str
    builder: Callable[[], CodeMaoRequest]
    runner: Callable[[CodeMaoSession], Awaitable[CommandResult]]


PROGRAM_ACTIONS: dict[str, ProgramAction] = {
    action.name: action
    for action in (
        ProgramAction(
            "start", lambda: control_program_request(start=True), CodeMaoSession.start_program
        ),
        ProgramAction(
            "stop", lambda: control_program_request(start=False), CodeMaoSession.stop_program
        ),
        ProgramAction("revert-origin", revert_origin_request, CodeMaoSession.revert_origin),
    )
}
"""dict[str, ProgramAction]: Registry of the actions that follow the handshake."""

DEFAULT_LAN_PORT_HINT = (
    "TCP port advertised by mDNS; without it pass --port explicitly "
    "(7000 is only the one value observed on hardware, not confirmed)"
)
"""str: Help text for ``--port``; the port is never assumed."""


# ----------------------------------------------------------------- factories
def create_ble_transport(address: str, *, trailer_size: int) -> FrameTransport:
    """Return the persistent BLE transport.

    Args:
        address (str): Platform address of the robot.
        trailer_size (int): Bytes to strip from each notification before reassembly.

    Returns:
        FrameTransport: A ``bleak``-backed transport.
    """
    return BleTransport(address, trailer_size=trailer_size)


def create_lan_transport(host: str, port: int) -> FrameTransport:
    """Return the LAN transport.

    Args:
        host (str): IP address of the robot.
        port (int): TCP port of the robot's control service.

    Returns:
        FrameTransport: An :mod:`asyncio` TCP transport that performs the app handshake.
    """
    return LanTransport(host, port)


def create_lan_scanner(interfaces: tuple[str, ...] = ()) -> LanScanner:
    """Return the mDNS scanner.

    Args:
        interfaces (tuple[str, ...]): Local addresses to bind multicast to; empty for the default.

    Returns:
        LanScanner: A ``zeroconf``-backed scanner.
    """
    return ZeroconfLanScanner(interfaces=interfaces)


def create_network_detector() -> WifiDetector:
    """Return the detector of the machine's current Wi-Fi network.

    Returns:
        WifiDetector: The operating-system detector.
    """
    return create_detector()


# ------------------------------------------------------------------- helpers
def _encode_request(
    action: str,
    *,
    user_id: str,
    nickname: str,
    programme_version: int,
) -> CodeMaoRequest:
    """Build the request shown by ``program encode``.

    Args:
        action (str): ``"handshake"`` or a key of ``PROGRAM_ACTIONS``.
        user_id (str): Handshake user id.
        nickname (str): Handshake nickname.
        programme_version (int): Handshake programme version.

    Returns:
        CodeMaoRequest: The request for ``action``.
    """
    if action == "handshake":
        return handshake_request(user_id, nickname, programme_version)
    return PROGRAM_ACTIONS[action].builder()


def _describe_event(reply: CodeMaoReply) -> str:
    """Summarise an unsolicited message for the console.

    Args:
        reply (CodeMaoReply): Message that did not answer a request.

    Returns:
        str: One line with the command id, serials and body in hexadecimal.
    """
    return (
        f"event commandId={reply.command_id} sendSerial={reply.send_serial} "
        f"responseSerial={reply.response_serial} body={reply.body.hex() or '(empty)'}"
    )


async def _run_session(  # noqa: PLR0913 - mirrors the command options
    transport: FrameTransport,
    *,
    user_id: str,
    nickname: str,
    programme_version: int,
    actions: tuple[str, ...],
    hold: float,
    reply_timeout: float,
    console: Console,
) -> list[tuple[str, CommandResult]]:
    """Connect, run the handshake, perform ``actions`` in order and optionally keep listening.

    The sequence stops at the first unsuccessful result.

    Args:
        transport (FrameTransport): Channel to the robot.
        user_id (str): Handshake user id.
        nickname (str): Handshake nickname.
        programme_version (int): Handshake programme version.
        actions (tuple[str, ...]): Keys of ``PROGRAM_ACTIONS`` to perform after the handshake.
        hold (float): Seconds to keep the session open and print events afterwards.
        reply_timeout (float): Seconds to wait for each reply.
        console (Console): Console receiving unsolicited events.

    Returns:
        list[tuple[str, CommandResult]]: Label and result of every step performed.
    """
    outcomes: list[tuple[str, CommandResult]] = []
    async with CodeMaoSession(
        transport,
        reply_timeout=reply_timeout,
        on_event=lambda reply: console.print(f"[dim]{escape(_describe_event(reply))}[/dim]"),
    ) as session:
        handshake = await session.handshake(user_id, nickname, programme_version)
        outcomes.append(("handshake", handshake))
        if not handshake.is_success:
            return outcomes
        for action in actions:
            result = await PROGRAM_ACTIONS[action].runner(session)
            outcomes.append((action, result))
            if not result.is_success:
                return outcomes
        if hold > 0:
            await session.listen(hold)
    return outcomes


# ---------------------------------------------------------------------- group
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def program() -> None:
    """Drive the robot in programming (CodeMao) mode, over BLE or on the local network.

    Returns:
        None: This function does not return a value.
    """


def _identity_options(function: Callable[..., None]) -> Callable[..., None]:
    """Attach the handshake options shared by ``encode`` and ``run``.

    Args:
        function (Callable[..., None]): Click callback to decorate.

    Returns:
        Callable[..., None]: The callback with the handshake options attached.
    """
    options = [
        click.option("--user-id", default="0", show_default=True, help="Handshake userId."),
        click.option(
            "--nickname", default="alpha-mini-py", show_default=True, help="Handshake nickName."
        ),
        click.option(
            "--programme-version",
            type=click.IntRange(min=0),
            required=True,
            help="Blockly programme version (programmeVersion); the app's value is not recovered.",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


@program.command("encode")
@_identity_options
@click.option(
    "--action",
    type=click.Choice(["handshake", *PROGRAM_ACTIONS]),
    default="handshake",
    show_default=True,
)
@click.option("--via", type=click.Choice(["ble", "lan"]), default="ble", show_default=True)
@click.option("--send-serial", type=click.IntRange(min=0), default=1, show_default=True)
@click.option("--random-id", type=click.IntRange(0, MAX_RANDOM_ID), default=0x1234)
@click.option("--output", "output_format", type=click.Choice(["pretty", "json"]), default="pretty")
@handle_errors
def encode_command(  # noqa: PLR0913 - one parameter per CLI option
    user_id: str,
    nickname: str,
    programme_version: int,
    action: str,
    via: str,
    send_serial: int,
    random_id: int,
    output_format: str,
) -> None:
    """Encode one programming command offline (nothing is sent anywhere).

    Args:
        user_id (str): Handshake user id.
        nickname (str): Handshake nickname.
        programme_version (int): Handshake programme version.
        action (str): Command to encode.
        via (str): Channel whose framing to show: ``"ble"`` (GATT writes) or ``"lan"`` (TCP).
        send_serial (int): Header ``sendSerial`` value.
        random_id (int): Chunk random id for the BLE writes (ignored with ``--via lan``).
        output_format (str): ``"pretty"`` or ``"json"``.

    Returns:
        None: This function does not return a value.
    """
    request = _encode_request(
        action, user_id=user_id, nickname=nickname, programme_version=programme_version
    )
    codec = CodeMaoCodec.for_lan() if via == "lan" else CodeMaoCodec()
    frame = codec.encode(request, send_serial)
    # The randomId/serialNo trailer exists only on GATT writes; a TCP stream has no chunks.
    chunks = TrailerChunker(FixedRandomId(random_id)).split(frame) if via == "ble" else []
    console = Console()
    if output_format == "json":
        document = {
            "commandId": request.command_id,
            "body": request.body.hex(),
            "frame": frame.hex(),
            "via": via,
            "chunks": [chunk.hex() for chunk in chunks] if via == "ble" else None,
        }
        console.print_json(json.dumps(document))
        return
    console.print(
        Panel(
            f"commandId={CodeMaoCommand(request.command_id).name} ({request.command_id})  "
            f"body={request.body.hex(' ') or '(empty)'}",
            title="Command",
            expand=False,
        )
    )
    console.print(Panel(frame.hex(" "), title=f"Frame ({len(frame)} bytes)", expand=False))
    if via == "ble":
        table = Table(
            "#", "Bytes", "GATT write value", title="GATT writes (BLE only, <= 20 B each)"
        )
        for serial, chunk in enumerate(chunks, 1):
            table.add_row(str(serial), str(len(chunk)), chunk.hex(" "))
        console.print(table)
    else:
        console.print("[dim]LAN: the frame above is written as-is to the TCP stream.[/dim]")
    console.print("[dim]Offline output only - nothing was sent to any device.[/dim]")


@program.command("run")
@_identity_options
@click.option("--via", type=click.Choice(["ble", "lan"]), default="ble", show_default=True)
@click.option("--device", default=None, help="BLE: robot address (see the 'scan' command).")
@click.option("--host", default=None, help="LAN: robot IP address (see 'lan-scan').")
@click.option("--port", type=click.IntRange(1, 65535), default=None, help=DEFAULT_LAN_PORT_HINT)
@click.option(
    "--do",
    "actions",
    type=click.Choice(sorted(PROGRAM_ACTIONS)),
    multiple=True,
    help="Action after the handshake; repeatable, executed in the given order.",
)
@click.option(
    "--hold",
    type=click.FloatRange(min=0),
    default=0.0,
    show_default=True,
    help="Seconds to keep the session open, printing robot events.",
)
@click.option(
    "--reply-timeout",
    type=click.FloatRange(min=0.1),
    default=DEFAULT_REPLY_TIMEOUT_SECONDS,
    show_default=True,
)
@click.option(
    "--raw-notifications",
    is_flag=True,
    help="BLE: notifications carry no randomId/serialNo trailer.",
)
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@handle_errors
def run_command(  # noqa: PLR0913 - one parameter per CLI option
    user_id: str,
    nickname: str,
    programme_version: int,
    via: str,
    device: str | None,
    host: str | None,
    port: int | None,
    actions: tuple[str, ...],
    hold: float,
    reply_timeout: float,
    raw_notifications: bool,  # noqa: FBT001
    assume_yes: bool,  # noqa: FBT001
) -> None:
    """Connect, start the programming skill and optionally run, stop or revert the program.

    The robot may move when ``--do start`` or ``--do revert-origin`` is used.

    Args:
        user_id (str): Handshake user id.
        nickname (str): Handshake nickname.
        programme_version (int): Handshake programme version.
        via (str): Channel, ``"ble"`` or ``"lan"``.
        device (str | None): BLE address, required with ``--via ble``.
        host (str | None): LAN address, required with ``--via lan``.
        port (int | None): LAN port, required with ``--via lan``.
        actions (tuple[str, ...]): Actions to perform after the handshake.
        hold (float): Seconds to keep listening after the actions.
        reply_timeout (float): Seconds to wait for each reply.
        raw_notifications (bool): Whether BLE notifications lack the trailer.
        assume_yes (bool): Skip the confirmation prompt.

    Returns:
        None: This function does not return a value.

    Raises:
        click.UsageError: If the options required by the channel are missing.
        UserAbortedError: If the confirmation is declined (translated to an abort).
        click.ClickException: If the robot reports an unsuccessful result.
    """
    console = Console()
    transport: FrameTransport
    if via == "ble":
        if device is None:
            msg = "--device is required with --via ble"
            raise click.UsageError(msg)
        transport = create_ble_transport(
            device, trailer_size=0 if raw_notifications else GATT_CHUNK_TRAILER_SIZE
        )
        target = f"BLE {device}"
    else:
        if host is None or port is None:
            msg = "--host and --port are required with --via lan"
            raise click.UsageError(msg)
        transport = create_lan_transport(host, port)
        target = f"LAN {host}:{port}"
    steps = ", ".join(["handshake", *actions])
    console.print(
        Panel(f"Robot: {escape(target)}\nSteps: {steps}", title="About to run", expand=False)
    )
    if not assume_yes and not click.confirm("Connect and run these steps?", default=False):
        raise UserAbortedError
    outcomes = asyncio.run(
        _run_session(
            transport,
            user_id=user_id,
            nickname=nickname,
            programme_version=programme_version,
            actions=actions,
            hold=hold,
            reply_timeout=reply_timeout,
            console=console,
        )
    )
    if isinstance(transport, LanTransport) and transport.handshake_result is not None:
        hello = transport.handshake_result
        console.print(
            f"[dim]robot: software={escape(hello.robot_soft_version_name)} "
            f"firmware={escape(hello.android_firmware_version)} "
            f"interface={hello.bt_interface_version} errorCode={hello.error_code} "
            f"sessionId={escape(hello.session_id)}[/dim]"
        )
    for label, result in outcomes:
        colour = "green" if result.is_success else "red"
        console.print(
            f"[{colour}]{escape(label)}[/{colour}]: success={result.is_success} "
            f"code={result.result_code} message={escape(result.message)!r}"
        )
    failed = [label for label, result in outcomes if not result.is_success]
    if failed:
        msg = f"the robot reported failure for: {', '.join(failed)}"
        raise click.ClickException(msg)


# ------------------------------------------------------------- scope guard
def _check_scope(expect_ssid: str | None, console: Console) -> None:
    """Abort when the machine is not on the expected Wi-Fi network.

    mDNS is link-local (UDP 5353, TTL 1): scanning from another network yields a false negative,
    so the scan is refused instead of reporting an empty result.

    Args:
        expect_ssid (str | None): SSID the robot's network is expected to have.
        console (Console): Console used for the report and warnings.

    Returns:
        None: This function does not return a value.

    Raises:
        click.ClickException: If the current SSID differs from ``expect_ssid``.
    """
    try:
        current = create_network_detector().detect().ssid
    except NetworkDetectionError as error:
        console.print(
            f"[yellow]warning: no active Wi-Fi detected ({escape(str(error))}); the scan is "
            "limited to the multicast IPv4 available on the selected interface.[/yellow]"
        )
        return
    expected = f" (expected: {escape(expect_ssid)})" if expect_ssid else ""
    console.print(f"ssid: {escape(current)}{expected}")
    if expect_ssid and current != expect_ssid:
        msg = (
            f"connected to {current!r}, not {expect_ssid!r}. mDNS does not cross networks, so "
            "scanning from here would give a false negative; join the robot's network first."
        )
        raise click.ClickException(msg)


def _print_report(report: DiscoveryReport, console: Console) -> None:
    """Print the discovery counters, the diagnostic and the resolved records.

    Args:
        report (DiscoveryReport): Result of the scan.
        console (Console): Console receiving the output.

    Returns:
        None: This function does not return a value.
    """
    by_type = ", ".join(f"{escape(t)}={n}" for t, n in sorted(report.seen_by_type.items()))
    console.print(f"types browsed: {len(report.types_browsed)}")
    console.print(f"types advertised by the meta-query: {len(report.advertised_types)}")
    console.print(f"records seen: {report.records_seen}" + (f" ({by_type})" if by_type else ""))
    console.print(f"resolved: {len(report.records)}")
    console.print(f"robots: {len(report.robots)}")
    console.print(f"diagnostic: {report.diagnostic}")
    if report.unresolved:
        console.print(f"unresolved: {escape(', '.join(report.unresolved))}")
    table = Table("Name", "Host", "Port", "Type", "TXT", title="Resolved services")
    for record in report.records:
        text = ", ".join(f"{k}={v}" for k, v in record.text.items())
        table.add_row(
            escape(record.name),
            escape(record.host),
            str(record.port),
            escape(record.service_type),
            escape(text),
        )
    console.print(table)


def _scan_options(function: Callable[..., None]) -> Callable[..., None]:
    """Attach the options shared by ``lan-scan`` and ``lan-diag``.

    Args:
        function (Callable[..., None]): Click callback to decorate.

    Returns:
        Callable[..., None]: The callback with the options attached.
    """
    options = [
        click.option(
            "--scan-timeout", type=click.FloatRange(min=1), default=12.0, show_default=True
        ),
        click.option(
            "--expect-ssid", default=None, help="Abort unless connected to this Wi-Fi network."
        ),
        click.option(
            "--interface",
            "interfaces",
            multiple=True,
            help="Local IPv4 address to bind multicast to; repeatable.",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


def _run_scan(
    scan_timeout: float, expect_ssid: str | None, interfaces: tuple[str, ...], console: Console
) -> DiscoveryReport:
    """Check the scope, then scan and print the report.

    Args:
        scan_timeout (float): Seconds to browse.
        expect_ssid (str | None): Expected SSID, if any.
        interfaces (tuple[str, ...]): Local addresses to bind to.
        console (Console): Console receiving the output.

    Returns:
        DiscoveryReport: The scan result.
    """
    _check_scope(expect_ssid, console)
    if interfaces:
        console.print(f"interface: {escape(', '.join(interfaces))}")
    with console.status(f"Browsing mDNS for {scan_timeout:g}s..."):
        report = asyncio.run(create_lan_scanner(interfaces).scan(scan_timeout))
    _print_report(report, console)
    return report


@click.command("lan-scan")
@_scan_options
@handle_errors
def lan_scan(scan_timeout: float, expect_ssid: str | None, interfaces: tuple[str, ...]) -> None:
    """List robots advertised over mDNS (all known service types, with a diagnostic).

    An empty result is inconclusive: it does not prove that the robot has mDNS disabled.

    Args:
        scan_timeout (float): Seconds to browse.
        expect_ssid (str | None): Abort unless connected to this network.
        interfaces (tuple[str, ...]): Local addresses to bind multicast to.

    Returns:
        None: This function does not return a value.
    """
    _run_scan(scan_timeout, expect_ssid, interfaces, Console())
