"""Command-line interface (click for parsing, rich for rendering)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from alpha_mini_wifi_encoder import __version__
from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.constants import MAX_RANDOM_ID, WifiCommand
from alpha_mini_wifi_encoder.encoder import WifiProvisioningEncoder
from alpha_mini_wifi_encoder.errors import EncoderError
from alpha_mini_wifi_encoder.ids import FixedRandomId, RandomIdProvider, SystemRandomId
from alpha_mini_wifi_encoder.models import WifiCredentials
from alpha_mini_wifi_encoder.network import Security, create_detector
from alpha_mini_wifi_encoder.serializers import (
    CompactJsonSerializer,
    GsonCompatSerializer,
    PayloadSerializer,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from alpha_mini_wifi_encoder.encoder import EncodedProvisioning

_SERIALIZERS: dict[str, Callable[[], PayloadSerializer]] = {
    "gson": GsonCompatSerializer,
    "compact": CompactJsonSerializer,
}


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


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
    """Merge explicit options with the detected current network (explicit wins)."""
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


def _render_pretty(console: Console, result: EncodedProvisioning, *, show_layers: bool) -> None:
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
    console.print("[dim]Offline output only - nothing was sent to any device.[/dim]")


def _render_json(console: Console, result: EncodedProvisioning) -> None:
    document = {
        "json": result.json_text,
        "frame": result.frame.hex(),
        "chunks": [chunk.hex() for chunk in result.chunks],
    }
    console.print_json(json.dumps(document))


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="alpha-mini-wifi-encode")
@click.option("--cap", "capabilities", default=None, help='Capabilities, e.g. "[WPA2-PSK-CCMP]".')
@click.option("--ssid", default=None, help="Network name.")
@click.option(
    "--current-network",
    is_flag=True,
    help="Use the Wi-Fi network this machine is connected to (SSID and security are detected; "
    "--ssid/--cap override). The password is asked for, never read from the OS.",
)
@click.option("--include-bssid", is_flag=True, help="With --current-network: also send the BSSID.")
@click.option(
    "--pwd",
    "password",
    default=None,
    envvar="ALPHA_MINI_WIFI_PASSWORD",
    show_envvar=True,
    help="Network password (empty = open network). Prefer the environment variable; "
    "with --current-network it is prompted for when omitted.",
)
@click.option("--bssid", default=None, help="Access-point MAC address.")
@click.option("--dont-ping", type=click.Choice(["0", "1"]), default=None, help="Optional 'v' flag.")
@click.option("--change-wifi", is_flag=True, help="Use co=2 (change Wi-Fi) instead of co=21.")
@click.option("--send-serial", type=click.IntRange(min=0), default=1, show_default=True)
@click.option(
    "--random-id",
    type=click.IntRange(0, MAX_RANDOM_ID),
    default=0x1234,
    show_default=True,
    help="Chunk random id (reproducible output).",
)
@click.option("--randomize", is_flag=True, help="Draw a random chunk id, as the app does.")
@click.option(
    "--json-style",
    type=click.Choice(sorted(_SERIALIZERS)),
    default="gson",
    show_default=True,
    help="'gson' escapes < > & = ' like Gson; 'compact' does not.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
    show_default=True,
)
@click.option("--layers", "show_layers", is_flag=True, help="Also show every layer's output.")
def main(  # noqa: PLR0913 - one parameter per CLI option
    capabilities: str | None,
    ssid: str | None,
    current_network: bool,  # noqa: FBT001
    include_bssid: bool,  # noqa: FBT001
    password: str | None,
    bssid: str | None,
    dont_ping: str | None,
    change_wifi: bool,  # noqa: FBT001
    send_serial: int,
    random_id: int,
    randomize: bool,  # noqa: FBT001
    json_style: str,
    output_format: str,
    show_layers: bool,  # noqa: FBT001
) -> None:
    """Encode a Wi-Fi provisioning request into BLE frames (offline, no BLE I/O)."""
    console = Console()
    try:
        credentials = _build_credentials(
            ssid=ssid,
            capabilities=capabilities,
            password=password,
            bssid=bssid,
            dont_ping=dont_ping,
            current_network=current_network,
            include_bssid=include_bssid,
        )
        ids: RandomIdProvider = SystemRandomId() if randomize else FixedRandomId(random_id)
        encoder = WifiProvisioningEncoder(_SERIALIZERS[json_style](), TrailerChunker(ids))
        command = (
            WifiCommand.WIFI_INFO_TRANS if change_wifi else WifiCommand.WIFI_INFO_TRANS_FOR_BINDING
        )
        result = encoder.encode(credentials, command=command, send_serial=send_serial)
    except ValidationError as error:
        raise click.UsageError(_format_validation_error(error)) from error
    except EncoderError as error:
        raise click.ClickException(str(error)) from error

    if output_format == "json":
        _render_json(console, result)
    else:
        _render_pretty(console, result, show_layers=show_layers)
