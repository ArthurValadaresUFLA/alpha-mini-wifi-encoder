import json

import pytest
from click.testing import CliRunner, Result

from alpha_mini_wifi_encoder import cli
from alpha_mini_wifi_encoder.ble import BleakDeviceScanner, BleakGattWriter, BleDevice
from alpha_mini_wifi_encoder.cli import main
from alpha_mini_wifi_encoder.errors import BleError, EncoderError, UserAbortedError
from alpha_mini_wifi_encoder.network import DetectedNetwork, NetworkDetectionError, Security
from alpha_mini_wifi_encoder.saved_networks import SavedNetwork
from tests.fakes import FakeDetector, FakePrompter, FakeSaved, FakeScanner, FakeWriter
from tests.helpers import decode_json_text, reassemble
from tests.test_responses_ble import notifications_for

ROBOT = BleDevice("AA:BB:CC:00:00:01", "Alpha Mini 1A2B", -50, advertises_service=True)
OTHER = BleDevice("11:22:33:44:55:66", "Headphones", -70)
CURRENT = DetectedNetwork("LaptopNet", Security.WPA2)


class Env:
    """Installs fakes in the cli module."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mp = monkeypatch
        self.writer = FakeWriter()
        self.scanner = FakeScanner([OTHER, ROBOT])
        self.prompter: FakePrompter | None = None
        self.prompter_requested = False
        self.install()

    def install(self) -> None:
        mp = self.mp
        mp.setattr("alpha_mini_wifi_encoder.cli.create_detector", lambda: FakeDetector(CURRENT))
        mp.setattr(
            "alpha_mini_wifi_encoder.cli.create_saved_networks",
            lambda: FakeSaved([SavedNetwork("Home", Security.WPA2), SavedNetwork("Legacy")]),
        )
        mp.setattr("alpha_mini_wifi_encoder.cli.create_scanner", lambda **_k: self.scanner)
        mp.setattr("alpha_mini_wifi_encoder.cli.create_writer", lambda: self.writer)
        mp.setattr("alpha_mini_wifi_encoder.cli.create_prompter", self._prompter)

    def _prompter(self) -> FakePrompter:
        self.prompter_requested = True
        if self.prompter is None:
            message = "interactive mode needs a terminal"
            raise EncoderError(message)
        return self.prompter

    def written_json(self) -> str:
        return decode_json_text(reassemble(self.writer.chunks))[0]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


def run(*args: str, input_: str | None = None) -> Result:
    return CliRunner().invoke(main, ["send", *args], input=input_)


def test_full_interactive_flow(env: Env) -> None:
    env.prompter = FakePrompter("Home", "hunter2", "Bind", "Alpha Mini", True)
    env.writer.notifications = notifications_for('{"co":102}')
    result = CliRunner().invoke(main, ["send"])
    assert result.exit_code == 0, result.output
    assert env.writer.address == ROBOT.address
    assert json.loads(env.written_json()) == {"c": "WPA2", "p": "hunter2", "s": "Home", "co": 21}
    assert "hunter2" not in result.output
    assert "Wi-Fi information accepted" in result.output
    assert "Payload written" in result.output
    assert env.writer.listen == 5.0


def test_interactive_other_network_open_change_wifi(env: Env) -> None:
    env.prompter = FakePrompter("another", "Cafe", "Open", "Change", "Alpha Mini", True)
    result = CliRunner().invoke(main, ["send"])
    assert result.exit_code == 0, result.output
    assert json.loads(env.written_json()) == {"c": "N", "p": "", "s": "Cafe", "co": 2}
    assert "(none)" in result.output


def test_interactive_saved_network_unknown_security(env: Env) -> None:
    env.prompter = FakePrompter("Legacy", "WPA2", "pw", "Bind", "Alpha Mini", True)
    assert CliRunner().invoke(main, ["send"]).exit_code == 0
    assert json.loads(env.written_json())["s"] == "Legacy"


def test_flags_with_interactive_flag_still_open_menus(env: Env) -> None:
    env.prompter = FakePrompter("Current", "Bind", "Alpha Mini", True)
    result = CliRunner().invoke(
        main,
        [
            "send",
            "-i",
            "--ssid",
            "Ignored",
            "--cap",
            "WPA",
            "--device",
            "X",
            "--pwd",
            "envpw",
            "--include-bssid",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(env.written_json())["s"] == "LaptopNet"


def test_rescan_then_select(env: Env) -> None:
    env.scanner = FakeScanner([], [ROBOT])
    env.install()
    env.prompter = FakePrompter("Home", "pw", "Bind", "Rescan", "Alpha Mini", True)
    assert CliRunner().invoke(main, ["send"]).exit_code == 0
    assert env.scanner.calls == 2


def test_non_interactive_needs_no_terminal(env: Env) -> None:
    result = run(
        "--ssid",
        "Net",
        "--cap",
        "WPA2",
        "--pwd",
        "pw",
        "--device",
        "AA:01",
        "--yes",
        "--listen",
        "0",
    )
    assert result.exit_code == 0, result.output
    assert not env.prompter_requested
    assert env.writer.address == "AA:01"
    assert env.writer.listen == 0
    assert env.scanner.calls == 0


def test_confirmation_via_click_and_decline(env: Env) -> None:
    base = ["--ssid", "Net", "--cap", "WPA2", "--pwd", "pw", "--device", "AA:01"]
    assert run(*base, input_="y\n").exit_code == 0
    declined = run(*base, input_="n\n")
    assert declined.exit_code == 1
    assert "Aborted" in declined.output


def test_dry_run_does_not_connect(env: Env) -> None:
    result = run("--ssid", "Net", "--cap", "WPA2", "--pwd", "pw", "--dry-run")
    assert result.exit_code == 0
    assert "nothing was sent" in result.output
    assert env.writer.address is None
    assert env.scanner.calls == 0


def test_current_network_flag(env: Env) -> None:
    result = run("--current-network", "--pwd", "pw", "--device", "AA:01", "--yes")
    assert result.exit_code == 0, result.output
    assert json.loads(env.written_json())["s"] == "LaptopNet"


def test_robot_error_response_fails(env: Env) -> None:
    env.writer.notifications = notifications_for('{"co":-1,"ec":7}')
    result = run("--ssid", "N", "--cap", "WPA2", "--pwd", "p", "--device", "A", "--yes")
    assert result.exit_code == 1
    assert "ec=7" in result.output


def test_no_response_and_undecodable_notifications(env: Env) -> None:
    args = ("--ssid", "N", "--cap", "WPA2", "--pwd", "p", "--device", "A", "--yes")
    assert "No response received" in run(*args).output
    env.writer.notifications = (b"\x01\x02",)
    assert "not decoded: 0102" in run(*args).output


def test_ble_error_is_reported(env: Env) -> None:
    env.writer.error = BleError("link lost")
    result = run("--ssid", "N", "--cap", "WPA2", "--pwd", "p", "--device", "A", "--yes")
    assert result.exit_code == 1
    assert "link lost" in result.output


def test_user_cancels_menu(env: Env) -> None:
    env.prompter = FakePrompter(UserAbortedError())
    result = CliRunner().invoke(main, ["send"])
    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_menu_without_terminal_is_a_clean_error(env: Env) -> None:
    result = CliRunner().invoke(main, ["send"])
    assert result.exit_code == 1
    assert "terminal" in result.output


def test_invalid_input_is_usage_error(env: Env) -> None:
    result = run("--ssid", "N", "--cap", "WPA2", "--bssid", "bad", "--device", "A", "--yes")
    assert result.exit_code == 2


def test_scan_command(env: Env) -> None:
    result = CliRunner().invoke(main, ["scan"])
    assert result.exit_code == 0
    assert "Alpha Mini 1A2B" in result.output
    assert "yes" in result.output


def test_default_factories_build_real_collaborators() -> None:
    assert isinstance(cli.create_scanner(include_unnamed=True), BleakDeviceScanner)
    assert isinstance(cli.create_writer(), BleakGattWriter)
    with pytest.raises(EncoderError, match="terminal"):
        cli.create_prompter()


def test_unsupported_os_falls_back_to_manual_network_entry(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported() -> None:
        message = "unsupported OS"
        raise NetworkDetectionError(message)

    monkeypatch.setattr("alpha_mini_wifi_encoder.cli.create_detector", unsupported)
    monkeypatch.setattr("alpha_mini_wifi_encoder.cli.create_saved_networks", unsupported)
    env.prompter = FakePrompter("another", "Manual", "WPA2", "pw", "Bind", "Alpha Mini", True)
    assert CliRunner().invoke(main, ["send"]).exit_code == 0
    assert json.loads(env.written_json())["s"] == "Manual"
