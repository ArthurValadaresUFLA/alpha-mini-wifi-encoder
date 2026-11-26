import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from alpha_mini_wifi_encoder import cli_program
from alpha_mini_wifi_encoder.ble_transport import BleTransport
from alpha_mini_wifi_encoder.cli import main
from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, handshake_request
from alpha_mini_wifi_encoder.constants import CodeMaoCommand
from alpha_mini_wifi_encoder.lan import LanTransport
from alpha_mini_wifi_encoder.lan_discovery import LanRobot, ZeroconfLanScanner
from tests.codemao_helpers import ScriptedTransport, default_responder, envelope

HANDSHAKE = ["--programme-version", "3", "--user-id", "u", "--nickname", "n"]


def invoke(*args: str) -> Result:
    return CliRunner().invoke(main, ["program", *args])


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.transport = ScriptedTransport()
        self.ble_args: tuple[str, int] | None = None
        self.lan_args: tuple[str, int] | None = None

        def ble(address: str, *, trailer_size: int) -> ScriptedTransport:
            self.ble_args = (address, trailer_size)
            return self.transport

        def lan(host: str, port: int) -> ScriptedTransport:
            self.lan_args = (host, port)
            return self.transport

        monkeypatch.setattr(cli_program, "create_ble_transport", ble)
        monkeypatch.setattr(cli_program, "create_lan_transport", lan)

    @property
    def commands(self) -> list[int]:
        return [r.command_id for r in self.transport.sent]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


class TestEncode:
    def test_pretty_matches_codec(self) -> None:
        result = invoke("encode", *HANDSHAKE)
        assert result.exit_code == 0, result.output
        frame = CodeMaoCodec().encode(handshake_request("u", "n", 3), 1)
        assert "HANDSHAKE (29)" in result.output
        assert frame.hex(" ")[:20] in result.output.replace("\n", " ").replace("│", "")
        assert "nothing was sent" in result.output

    @pytest.mark.parametrize(
        ("action", "command", "body"),
        [
            ("handshake", 29, "0a016e12017518 03".replace(" ", "")),
            ("start", 33, "0801"),
            ("stop", 33, ""),
            ("revert-origin", 28, ""),
        ],
    )
    def test_json(self, action: str, command: int, body: str) -> None:
        result = invoke("encode", *HANDSHAKE, "--action", action, "--output", "json")
        assert result.exit_code == 0, result.output
        document = json.loads(result.output)
        assert (document["commandId"], document["body"]) == (command, body)
        assert document["frame"].startswith("fbbf01")
        assert all(len(chunk) <= 40 for chunk in document["chunks"])
        assert document["via"] == "ble"

    def test_pretty_for_empty_body(self) -> None:
        result = invoke("encode", *HANDSHAKE, "--action", "revert-origin")
        assert result.exit_code == 0
        assert "(empty)" in result.output
        assert CodeMaoCommand.REVERT_ORIGIN.name in result.output

    def test_programme_version_is_required(self) -> None:
        result = invoke("encode")
        assert result.exit_code == 2
        assert "--programme-version" in result.output


class TestRun:
    def test_ble_full_flow(self, env: Env) -> None:
        result = invoke(
            "run", *HANDSHAKE, "--device", "AA:BB", "--do", "start", "--do", "stop",
            "--do", "revert-origin", "--yes",
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert env.ble_args == ("AA:BB", 4)
        assert env.commands == [29, 33, 33, 28]
        assert result.output.count("success=True") == 4

    def test_raw_notifications_disable_the_trailer(self, env: Env) -> None:
        result = invoke("run", *HANDSHAKE, "--device", "AA", "--raw-notifications", "-y")
        assert result.exit_code == 0, result.output
        assert env.ble_args == ("AA", 0)

    def test_lan_flow(self, env: Env) -> None:
        result = invoke(
            "run", *HANDSHAKE, "--via", "lan", "--host", "10.0.0.2", "--port", "9000",
            "--do", "start", "-y",
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert env.lan_args == ("10.0.0.2", 9000)
        assert env.commands == [29, 33]

    def test_handshake_only_by_default(self, env: Env) -> None:
        assert invoke("run", *HANDSHAKE, "--device", "AA", "-y").exit_code == 0
        assert env.commands == [29]

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ([], "--device is required"),
            (["--via", "lan", "--host", "h"], "--host and --port are required"),
            (["--via", "lan", "--port", "1"], "--host and --port are required"),
        ],
    )
    def test_missing_target_options(self, env: Env, args: list[str], message: str) -> None:
        result = invoke("run", *HANDSHAKE, *args, "-y")
        assert result.exit_code == 2
        assert message in result.output
        assert env.commands == []

    def test_declined_confirmation_sends_nothing(self, env: Env) -> None:
        result = CliRunner().invoke(
            main, ["program", "run", *HANDSHAKE, "--device", "AA"], input="n\n"
        )
        assert result.exit_code == 1
        assert "About to run" in result.output
        assert env.commands == []

    def test_accepted_confirmation(self, env: Env) -> None:
        result = CliRunner().invoke(
            main, ["program", "run", *HANDSHAKE, "--device", "AA"], input="y\n"
        )
        assert result.exit_code == 0, result.output
        assert env.commands == [29]

    def test_failed_handshake_skips_actions(self, env: Env) -> None:
        env.transport.responder = default_responder(frozenset({CodeMaoCommand.HANDSHAKE}))
        result = invoke("run", *HANDSHAKE, "--device", "AA", "--do", "start", "-y")
        assert result.exit_code == 1
        assert "failure for: handshake" in result.output
        assert env.commands == [29]

    def test_failed_action_stops_the_sequence(self, env: Env) -> None:
        env.transport.responder = default_responder(
            frozenset({CodeMaoCommand.CONTROL_RUNNING_PROGRAM})
        )
        result = invoke(
            "run", *HANDSHAKE, "--device", "AA", "--do", "start", "--do", "revert-origin", "-y"
        )
        assert result.exit_code == 1
        assert "failure for: start" in result.output
        assert env.commands == [29, 33]

    def test_hold_prints_robot_events(self, env: Env) -> None:
        env.transport.inbox.put_nowait(envelope(600, b"\x01", send_serial=5))
        result = invoke("run", *HANDSHAKE, "--device", "AA", "--hold", "0.05", "-y")
        assert result.exit_code == 0, result.output
        assert "event commandId=600" in result.output

    def test_library_errors_become_click_errors(self, env: Env) -> None:
        env.transport.responder = lambda _request: []
        result = invoke("run", *HANDSHAKE, "--device", "AA", "--reply-timeout", "0.1", "-y")
        assert result.exit_code == 1
        assert "no reply to command 29" in result.output


class TestLanScan:
    def test_lists_robots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Scanner:
            async def scan(self, timeout: float) -> list[LanRobot]:
                return [LanRobot("mini-1", "10.0.0.2", 9000)]

        monkeypatch.setattr(cli_program, "create_lan_scanner", Scanner)
        result = CliRunner().invoke(main, ["lan-scan", "--scan-timeout", "1"])
        assert result.exit_code == 0, result.output
        assert "mini-1" in result.output
        assert "9000" in result.output


class TestLanEncode:
    def test_encode_lan_has_checksum_and_no_gatt_chunks(self) -> None:
        result = invoke("encode", *HANDSHAKE, "--via", "lan", "--output", "json")
        assert result.exit_code == 0, result.output
        document = json.loads(result.output)
        frame = bytes.fromhex(document["frame"])
        assert document["via"] == "lan"
        assert document["chunks"] is None
        assert frame[-1] == 0xED
        assert frame[-2] == (int.from_bytes(frame[3:5], "big") + 2) & 0xFF
        assert frame == CodeMaoCodec.for_lan().encode(handshake_request("u", "n", 3), 1)

    def test_encode_lan_pretty_has_no_gatt_table(self) -> None:
        result = invoke("encode", *HANDSHAKE, "--via", "lan")
        assert result.exit_code == 0, result.output
        assert "GATT" not in result.output
        assert "TCP stream" in result.output

    def test_encode_ble_json_keeps_chunks(self) -> None:
        document = json.loads(invoke("encode", *HANDSHAKE, "--output", "json").output)
        assert document["via"] == "ble"
        assert document["chunks"]


def test_default_factories() -> None:
    kwargs: dict[str, Any] = {"trailer_size": 4}
    assert isinstance(cli_program.create_ble_transport("AA", **kwargs), BleTransport)
    assert isinstance(cli_program.create_lan_transport("h", 1), LanTransport)
    assert isinstance(cli_program.create_lan_scanner(), ZeroconfLanScanner)
