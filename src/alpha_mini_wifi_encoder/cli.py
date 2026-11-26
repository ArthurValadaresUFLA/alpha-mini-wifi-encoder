"""Command-line interface for provisioning a UBTECH Alpha Mini over Bluetooth Low Energy.

The module exports the :func:`main` click group and its three commands:
:func:`encode` builds a payload offline, :func:`scan` discovers nearby devices
and :func:`send` writes the credentials to a robot. Only :func:`scan` and
:func:`send` perform BLE I/O; :func:`encode` is entirely offline.

It also exports the injectable factories :func:`create_prompter`,
:func:`create_scanner` and :func:`create_writer`, which return a
:class:`~.interactive.Prompter`, a :class:`~.ble.DeviceScanner` and a
:class:`~.ble.GattWriter` respectively. Tests can substitute these factories
to keep the command bodies free of concrete classes.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from alpha_mini_wifi_encoder import __version__
from alpha_mini_wifi_encoder.ble import BleakDeviceScanner, BleakGattWriter, BleDevice
from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.cli_program import lan_diag, lan_scan, program
from alpha_mini_wifi_encoder.cli_support import handle_errors
from alpha_mini_wifi_encoder.constants import MAX_RANDOM_ID, WifiCommand
from alpha_mini_wifi_encoder.encoder import WifiProvisioningEncoder
from alpha_mini_wifi_encoder.errors import UserAbortedError
from alpha_mini_wifi_encoder.ids import FixedRandomId, RandomIdProvider, SystemRandomId
from alpha_mini_wifi_encoder.interactive import (
    Prompter,
    QuestionaryPrompter,
    ask_network_password,
    select_device,
    select_network,
)
from alpha_mini_wifi_encoder.models import WifiCredentials
from alpha_mini_wifi_encoder.network import (
    NetworkDetectionError,
    Security,
    create_detector,
)
from alpha_mini_wifi_encoder.responses import decode_notifications
from alpha_mini_wifi_encoder.saved_networks import create_saved_networks
from alpha_mini_wifi_encoder.serializers import (
    CompactJsonSerializer,
    GsonCompatSerializer,
    PayloadSerializer,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from alpha_mini_wifi_encoder.ble import DeviceScanner, GattWriter
    from alpha_mini_wifi_encoder.encoder import EncodedProvisioning

_SERIALIZERS: dict[str, Callable[[], PayloadSerializer]] = {
    "gson": GsonCompatSerializer,
    "compact": CompactJsonSerializer,
}
"""dict[str, Callable[[], PayloadSerializer]]: Registry of JSON styles and serialisers."""
_DEFAULT_FIXED_RANDOM_ID = 0x1234
"""int: Default chunk random id used by ``encode`` for reproducible output."""


# ----------------------------------------------------------------- factories
# Module-level factories keep the command bodies free of concrete classes and
# let tests substitute fakes.
def create_prompter() -> Prompter:
    """Return the interactive prompter.

    Returns:
        Prompter: A ``questionary``-backed prompter for menus and hidden input.

    Raises:
        EncoderError: If standard input or output is not an interactive terminal.
    """
    return QuestionaryPrompter()


def create_scanner(*, include_unnamed: bool = False) -> DeviceScanner:
    """Return the BLE device scanner.

    Args:
        include_unnamed (bool): Whether to keep devices that advertise no name.

    Returns:
        DeviceScanner: A ``bleak``-backed scanner for nearby Bluetooth devices.
    """
    return BleakDeviceScanner(include_unnamed=include_unnamed)


def create_writer() -> GattWriter:
    """Return the BLE GATT writer.

    Returns:
        GattWriter: A ``bleak``-backed writer for GATT chunks and notifications.
    """
    return BleakGattWriter()


# ------------------------------------------------------------------- options
def _common_options(function: Callable[..., Any]) -> Callable[..., Any]:
    """Attach the network and encoding options shared by ``encode`` and ``send``.

    Args:
        function (Callable[..., Any]): Click callback to decorate with the options.

    Returns:
        Callable[..., Any]: The callback with every shared option attached.
    """
    options = [
        click.option(
            "--cap", "capabilities", default=None, help='Capabilities, e.g. "[WPA2-PSK-CCMP]".'
        ),
        click.option("--ssid", default=None, help="Network name."),
        click.option(
            "--current-network",
            is_flag=True,
            help="Use the Wi-Fi network this machine is connected to (SSID and security are "
            "detected; --ssid/--cap override). The password is asked for, never read from the OS.",
        ),
        click.option(
            "--include-bssid", is_flag=True, help="With --current-network: also send the BSSID."
        ),
        click.option(
            "--pwd",
            "password",
            default=None,
            envvar="ALPHA_MINI_WIFI_PASSWORD",
            show_envvar=True,
            help="Network password (empty = open network). Prefer the environment variable; "
            "it is prompted for (hidden) when omitted and needed.",
        ),
        click.option("--bssid", default=None, help="Access-point MAC address."),
        click.option(
            "--dont-ping", type=click.Choice(["0", "1"]), default=None, help="Optional 'v' flag."
        ),
        click.option(
            "--change-wifi/--bind",
            "change_wifi",
            default=None,
            help="co=2 (change Wi-Fi of a bound robot) or co=21 (bind, the default).",
        ),
        click.option("--send-serial", type=click.IntRange(min=0), default=1, show_default=True),
        click.option(
            "--random-id",
            type=click.IntRange(0, MAX_RANDOM_ID),
            default=None,
            help="Fixed chunk random id. Default: 0x1234 for 'encode', random for 'send'.",
        ),
        click.option(
            "--json-style",
            type=click.Choice(sorted(_SERIALIZERS)),
            default="gson",
            show_default=True,
            help="'gson' escapes < > & = ' like Gson; 'compact' does not.",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


# ------------------------------------------------------------------- helpers
def _build_credentials(  # noqa: PLR0913 - mirrors the CLI options
    *,
    ssid: str | None,
    capabilities: str | None,
    password: str | None,
    bssid: str | None,
    dont_ping: str | None,
    current_network: bool,
    include_bssid: bool,
) -> WifiCredentials:
    """Merge explicit options with the detected current network (explicit wins).

    Args:
        ssid (str | None): Network name from ``--ssid``.
        capabilities (str | None): Capability string from ``--cap``.
        password (str | None): Password from ``--pwd`` or the environment.
        bssid (str | None): Access-point MAC address from ``--bssid``.
        dont_ping (str | None): Wire value for the optional ``v`` flag.
        current_network (bool): Whether to detect the machine's current network.
        include_bssid (bool): Whether to include the detected BSSID.

    Returns:
        WifiCredentials: Validated credentials built from the merged values.

    Raises:
        NetworkDetectionError: If the current network cannot be detected.
        click.UsageError: If ``--ssid`` and ``--cap`` are missing without ``--current-network``.
        click.Abort: If the user aborts the hidden password prompt.
        ValidationError: If the merged values do not form valid credentials.
    """
    if current_network:
        detected = create_detector().detect()
        Console(stderr=True).print(
            f"Using current network {escape(detected.ssid)!r} ({detected.security.name})",
            highlight=False,
        )
        ssid = ssid or detected.ssid
        capabilities = capabilities or detected.security.value
        if bssid is None and include_bssid:
            bssid = detected.bssid
        if password is None:
            password = (
                ""
                if detected.security is Security.OPEN
                else click.prompt(
                    f"Password for {ssid!r}",
                    hide_input=True,
                    default="",
                    show_default=False,
                    err=True,
                )
            )
    elif ssid is None or capabilities is None:
        msg = "--ssid and --cap are required unless --current-network is used"
        raise click.UsageError(msg)
    return WifiCredentials.model_validate(
        {
            "ssid": ssid,
            "capabilities": capabilities,
            "password": password or "",
            "bssid": bssid,
            "dont_ping": dont_ping,
        },
    )


def _make_encoder(json_style: str, ids: RandomIdProvider) -> WifiProvisioningEncoder:
    """Build an encoder with the selected serialisation style and random ids.

    Args:
        json_style (str): Key into ``_SERIALIZERS`` from ``--json-style``.
        ids (RandomIdProvider): Provider of chunk random ids.

    Returns:
        WifiProvisioningEncoder: Encoder using the chosen serialiser and trailer chunker.

    Raises:
        KeyError: If ``json_style`` is not a recognised serialisation style.
    """
    return WifiProvisioningEncoder(_SERIALIZERS[json_style](), TrailerChunker(ids))


def _command(*, change_wifi: bool | None) -> WifiCommand:
    """Map the ``--change-wifi`` flag to the corresponding ``co`` command.

    Args:
        change_wifi (bool | None): ``True`` for ``co=2``, ``False`` or ``None`` for ``co=21``.

    Returns:
        WifiCommand: The command value to embed in the payload.
    """
    return WifiCommand.WIFI_INFO_TRANS if change_wifi else WifiCommand.WIFI_INFO_TRANS_FOR_BINDING


def _render_pretty(console: Console, result: EncodedProvisioning, *, show_layers: bool) -> None:
    """Print the encoded payload, layers and GATT writes as Rich panels and tables.

    Args:
        console (Console): Rich console on which to print.
        result (EncodedProvisioning): Encoded artefacts to display.
        show_layers (bool): Whether to include the per-layer table.

    Returns:
        None: This function does not return a value.
    """
    console.print(Panel(Syntax(result.json_text, "json", word_wrap=True), title="JSON payload"))
    if show_layers:
        layers = Table("Layer", "Bytes", "Hex", title="Layers")
        for layer in result.layers:
            layers.add_row(escape(layer.name), str(len(layer.data)), layer.data.hex(" "))
        console.print(layers)
    console.print(
        Panel(result.frame.hex(" "), title=f"Frame ({len(result.frame)} bytes)", expand=False),
    )
    chunks = Table("#", "Bytes", "GATT write value", title="GATT writes (<= 20 B each)")
    for serial, chunk in enumerate(result.chunks, 1):
        chunks.add_row(str(serial), str(len(chunk)), chunk.hex(" "))
    console.print(chunks)


def _render_json(console: Console, result: EncodedProvisioning) -> None:
    """Print the encoded artefacts as one JSON document.

    Args:
        console (Console): Rich console on which to print.
        result (EncodedProvisioning): Encoded artefacts to serialise as JSON.

    Returns:
        None: This function does not return a value.
    """
    document = {
        "json": result.json_text,
        "frame": result.frame.hex(),
        "chunks": [chunk.hex() for chunk in result.chunks],
    }
    console.print_json(json.dumps(document))


def _device_table(devices: list[BleDevice]) -> Table:
    """Build a Rich table describing the devices found by a scan.

    Args:
        devices (list[BleDevice]): Devices to list, in display order.

    Returns:
        Table: A table with name, address, signal strength and service columns.
    """
    table = Table("Name", "Address", "RSSI", "Alpha Mini service", title="Bluetooth devices")
    for device in devices:
        table.add_row(
            escape(device.name or "(unnamed)"),
            escape(device.address),
            "?" if device.rssi is None else f"{device.rssi} dBm",
            "yes" if device.advertises_service else "",
        )
    return table


def _scan(console: Console, scanner: DeviceScanner, timeout: float) -> list[BleDevice]:
    """Run a Bluetooth scan and return the devices found.

    Args:
        console (Console): Rich console used for the status indicator.
        scanner (DeviceScanner): Scanner to run.
        timeout (float): Scan duration in seconds.

    Returns:
        list[BleDevice]: Devices reported by the scanner.

    Raises:
        BleError: If the underlying scanner fails; propagated to the caller.
    """
    with console.status(f"Scanning Bluetooth for {timeout:g}s..."):
        return asyncio.run(scanner.scan(timeout))


def _safe[T](factory: Callable[[], T]) -> T | None:
    """Return ``factory()`` or ``None`` when the OS is unsupported.

    Args:
        factory (Callable[[], T]): Zero-argument factory to call, such as a detector.

    Returns:
        T | None: The factory's result, or ``None`` on ``NetworkDetectionError``.
    """
    try:
        return factory()
    except NetworkDetectionError:
        return None


# ---------------------------------------------------------------------- group
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="alpha-mini-wifi-encode")
def main() -> None:
    """Encode and send Wi-Fi provisioning payloads to a UBTECH Alpha Mini over BLE.

    Returns:
        None: This function does not return a value.
    """


main.add_command(program)
main.add_command(lan_scan)
main.add_command(lan_diag)


# --------------------------------------------------------------------- encode
@main.command()
@_common_options
@click.option("--randomize", is_flag=True, help="Draw a random chunk id, as the app does.")
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
    show_default=True,
)
@click.option("--layers", "show_layers", is_flag=True, help="Also show every layer's output.")
@handle_errors
def encode(  # noqa: PLR0913 - one parameter per CLI option
    capabilities: str | None,
    ssid: str | None,
    current_network: bool,  # noqa: FBT001
    include_bssid: bool,  # noqa: FBT001
    password: str | None,
    bssid: str | None,
    dont_ping: str | None,
    change_wifi: bool | None,  # noqa: FBT001
    send_serial: int,
    random_id: int | None,
    json_style: str,
    randomize: bool,  # noqa: FBT001
    output_format: str,
    show_layers: bool,  # noqa: FBT001
) -> None:
    """Encode a Wi-Fi provisioning request offline (nothing is sent anywhere).

    Args:
        capabilities (str | None): Capability string from ``--cap``.
        ssid (str | None): Network name from ``--ssid``.
        current_network (bool): Detect the network this machine is connected to.
        include_bssid (bool): Include the detected BSSID with ``--current-network``.
        password (str | None): Password from ``--pwd`` or the environment.
        bssid (str | None): Access-point MAC address from ``--bssid``.
        dont_ping (str | None): Wire value for the optional ``v`` flag.
        change_wifi (bool | None): ``True`` for ``co=2``, ``False`` or ``None`` for ``co=21``.
        send_serial (int): Header ``sendSerial`` value.
        random_id (int | None): Fixed chunk random id, or ``None`` for the default.
        json_style (str): Serialisation style key from ``--json-style``.
        randomize (bool): Draw a random chunk id instead of using a fixed one.
        output_format (str): Output style, either ``"pretty"`` or ``"json"``.
        show_layers (bool): Also display each layer's output in pretty mode.

    Returns:
        None: This function does not return a value.

    Raises:
        click.UsageError: If network options are missing or validation fails.
        ValidationError: If Pydantic rejects the credentials (translated by ``handle_errors``).
        EncoderError: If encoding fails (translated by ``handle_errors``).
    """
    console = Console()
    credentials = _build_credentials(
        ssid=ssid,
        capabilities=capabilities,
        password=password,
        bssid=bssid,
        dont_ping=dont_ping,
        current_network=current_network,
        include_bssid=include_bssid,
    )
    ids: RandomIdProvider = (
        SystemRandomId()
        if randomize
        else FixedRandomId(_DEFAULT_FIXED_RANDOM_ID if random_id is None else random_id)
    )
    result = _make_encoder(json_style, ids).encode(
        credentials,
        command=_command(change_wifi=change_wifi),
        send_serial=send_serial,
    )
    if output_format == "json":
        _render_json(console, result)
    else:
        _render_pretty(console, result, show_layers=show_layers)
        console.print("[dim]Offline output only - nothing was sent to any device.[/dim]")


# ----------------------------------------------------------------------- scan
@main.command()
@click.option("--scan-timeout", type=click.FloatRange(min=1), default=8.0, show_default=True)
@click.option("--all-devices", is_flag=True, help="Also list devices without a name.")
@handle_errors
def scan(scan_timeout: float, all_devices: bool) -> None:  # noqa: FBT001
    """List nearby Bluetooth Low Energy devices.

    Args:
        scan_timeout (float): Duration of the scan in seconds.
        all_devices (bool): Whether to include devices that advertise no name.

    Returns:
        None: This function does not return a value.

    Raises:
        BleError: If the Bluetooth scan fails (translated to a click error).
    """
    console = Console()
    devices = _scan(console, create_scanner(include_unnamed=all_devices), scan_timeout)
    console.print(_device_table(devices))


# ----------------------------------------------------------------------- send
@main.command()
@_common_options
@click.option("--device", default=None, help="Robot address (skips the device menu).")
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Always show the device and network menus (also used when flags are missing).",
)
@click.option("--scan-timeout", type=click.FloatRange(min=1), default=8.0, show_default=True)
@click.option("--all-devices", is_flag=True, help="Also list devices without a name.")
@click.option("--dry-run", is_flag=True, help="Show the bytes but do not connect or send.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@click.option(
    "--listen",
    type=click.FloatRange(min=0),
    default=5.0,
    show_default=True,
    help="Seconds to wait for the robot's notifications after writing.",
)
@handle_errors
def send(  # noqa: PLR0913 - one parameter per CLI option
    capabilities: str | None,
    ssid: str | None,
    current_network: bool,  # noqa: FBT001
    include_bssid: bool,  # noqa: FBT001
    password: str | None,
    bssid: str | None,
    dont_ping: str | None,
    change_wifi: bool | None,  # noqa: FBT001
    send_serial: int,
    random_id: int | None,
    json_style: str,
    device: str | None,
    interactive: bool,  # noqa: FBT001
    scan_timeout: float,
    all_devices: bool,  # noqa: FBT001
    dry_run: bool,  # noqa: FBT001
    assume_yes: bool,  # noqa: FBT001
    listen: float,
) -> None:
    """Send the Wi-Fi credentials to the robot over BLE.

    Missing options (or -i) open arrow-key menus to pick the robot and the network.
    The password is kept in memory only: it is never written to disk.

    Args:
        capabilities (str | None): Capability string from ``--cap``.
        ssid (str | None): Network name from ``--ssid``.
        current_network (bool): Detect the network this machine is connected to.
        include_bssid (bool): Include the detected BSSID with ``--current-network``.
        password (str | None): Password from ``--pwd`` or the environment.
        bssid (str | None): Access-point MAC address from ``--bssid``.
        dont_ping (str | None): Wire value for the optional ``v`` flag.
        change_wifi (bool | None): ``True`` for ``co=2``, ``False`` or ``None`` for ``co=21``.
        send_serial (int): Header ``sendSerial`` value.
        random_id (int | None): Fixed chunk random id, or ``None`` for a random one.
        json_style (str): Serialisation style key from ``--json-style``.
        device (str | None): Robot address from ``--device``, skipping the device menu.
        interactive (bool): Always show the device and network menus.
        scan_timeout (float): Duration of each device scan in seconds.
        all_devices (bool): Whether to include devices that advertise no name.
        dry_run (bool): Show the encoded bytes without connecting or sending.
        assume_yes (bool): Skip the final confirmation prompt.
        listen (float): Seconds to listen for the robot's notifications after writing.

    Returns:
        None: This function does not return a value.

    Raises:
        click.UsageError: If explicit network options are missing or validation fails.
        ValidationError: If Pydantic rejects the credentials (translated by ``handle_errors``).
        UserAbortedError: If a prompt is cancelled or confirmation is declined (translated).
        BleError: If scanning or writing fails (translated to a click error).
        click.ClickException: If the robot reports an error in its notifications.
    """
    console = Console()
    explicit_network = current_network or (ssid is not None and capabilities is not None)
    menu_network = interactive or not explicit_network
    menu_device = not dry_run and (interactive or device is None)
    prompter: Prompter | None = create_prompter() if menu_network or menu_device else None

    # 1. Network -----------------------------------------------------------
    if menu_network:
        assert prompter is not None  # noqa: S101 - guaranteed by the condition above
        choice = select_network(
            prompter,
            _safe(create_detector),
            _safe(create_saved_networks),
        )
        secret = password if password is not None else ask_network_password(prompter, choice)
        credentials = WifiCredentials(
            ssid=choice.ssid,
            capabilities=choice.security.value,
            password=secret,
            bssid=choice.bssid if include_bssid else None,
            dont_ping=dont_ping,  # type: ignore[arg-type]
        )
        if change_wifi is None:
            change_wifi = prompter.select(
                "What is this for?",
                [
                    ("Bind a new robot (co=21)", False),
                    ("Change the Wi-Fi of a bound robot (co=2)", True),
                ],
            )
    else:
        credentials = _build_credentials(
            ssid=ssid,
            capabilities=capabilities,
            password=password,
            bssid=bssid,
            dont_ping=dont_ping,
            current_network=current_network,
            include_bssid=include_bssid,
        )

    # 2. Encode ------------------------------------------------------------
    ids: RandomIdProvider = SystemRandomId() if random_id is None else FixedRandomId(random_id)
    result = _make_encoder(json_style, ids).encode(
        credentials,
        command=_command(change_wifi=change_wifi),
        send_serial=send_serial,
    )
    if dry_run:
        _render_pretty(console, result, show_layers=False)
        console.print(
            "[yellow]Dry run: the bytes above contain the password; nothing was sent.[/yellow]"
        )
        return

    # 3. Device ------------------------------------------------------------
    scanner = create_scanner(include_unnamed=all_devices)
    if menu_device:
        assert prompter is not None  # noqa: S101
        target = select_device(prompter, lambda: _scan(console, scanner, scan_timeout))
    else:
        target = BleDevice(address=str(device), name="")

    # 4. Confirm -----------------------------------------------------------
    command_name = _command(change_wifi=change_wifi).name
    console.print(
        Panel(
            f"Robot:    {escape(target.label)}\n"
            f"Network:  {escape(credentials.ssid)} ({credentials.capabilities})\n"
            f"Password: {'(none)' if not credentials.password else '********'}\n"
            f"Command:  {command_name}  |  {len(result.chunks)} GATT writes",
            title="About to send",
            expand=False,
        ),
    )
    if not assume_yes:
        question = "Send these Wi-Fi credentials to the robot?"
        approved = (
            prompter.confirm(question, default=True)
            if prompter is not None
            else click.confirm(question, default=False)
        )
        if not approved:
            raise UserAbortedError

    # 5. Send --------------------------------------------------------------
    writer = create_writer()
    with Progress(
        SpinnerColumn(),
        TextColumn("Writing to robot"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("write", total=len(result.chunks))
        notifications = asyncio.run(
            writer.send(
                target.address,
                result.chunks,
                listen_seconds=listen,
                on_progress=lambda done, _total: progress.update(task, completed=done),
            ),
        )

    # 6. Report ------------------------------------------------------------
    responses = decode_notifications(notifications)
    for response in responses:
        console.print(f"[bold]Robot:[/bold] {escape(response.describe())}")
    failed = [r for r in responses if r.is_error]
    if failed:
        msg = failed[0].describe()
        raise click.ClickException(msg)
    if not responses:
        note = (
            f"{len(notifications)} notification(s) received but not decoded: "
            + ", ".join(n.hex() for n in notifications)
            if notifications
            else "No response received (this does not necessarily mean failure)."
        )
        console.print(f"[yellow]{escape(note)}[/yellow]")
    console.print("[green]Payload written.[/green]")
