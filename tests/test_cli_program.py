import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from alpha_mini_wifi_encoder import cli_program
from alpha_mini_wifi_encoder.ble_transport import BleTransport
from alpha_mini_wifi_encoder.cli import main
from alpha_mini_wifi_encoder.codemao import (
    CodeMaoCodec,
    LanHandshakeResult,
    handshake_request,
    parse_lan_handshake_result,
)
from alpha_mini_wifi_encoder.codemao_messages import LanHandshakeResponse
from alpha_mini_wifi_encoder.constants import CodeMaoCommand
from alpha_mini_wifi_encoder.lan import LanTransport
from alpha_mini_wifi_encoder.lan_discovery import DiscoveryReport, LanRobot, ZeroconfLanScanner
from alpha_mini_wifi_encoder.network import DetectedNetwork, NetworkDetectionError, Security
from alpha_mini_wifi_encoder.port_probe import PortResult
from tests.codemao_helpers import ScriptedTransport, default_responder, envelope

REAL_NETWORK_DETECTOR_FACTORY = cli_program.create_network_detector

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


class FakeDetector:
    def __init__(self, ssid: str | None) -> None:
        self.ssid = ssid

    def detect(self) -> DetectedNetwork:
        if self.ssid is None:
            msg = "no Wi-Fi"
            raise NetworkDetectionError(msg)
        return DetectedNetwork(self.ssid, Security.WPA2)


@pytest.fixture(autouse=True)
def on_demo_neuron(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real OS: the machine is on ``demo_neuron`` unless a test says otherwise."""
    monkeypatch.setattr(cli_program, "create_network_detector", lambda: FakeDetector("demo_neuron"))


def report(**kwargs: Any) -> DiscoveryReport:
    values: dict[str, Any] = {
        "types_browsed": ("_a._tcp.local.", "_b._tcp.local."),
        "seen_by_type": {},
        "advertised_types": (),
        "records": (),
        "unresolved": (),
    }
    return DiscoveryReport(**{**values, **kwargs})


class Scanners:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, result: DiscoveryReport) -> None:
        self.interfaces: list[tuple[str, ...]] = []
        self.scans = 0
        outer = self

        class Scanner:
            async def scan(self, timeout: float) -> DiscoveryReport:
                outer.scans += 1
                return result

        def create(interfaces: tuple[str, ...] = ()) -> Scanner:
            self.interfaces.append(interfaces)
            return Scanner()

        monkeypatch.setattr(cli_program, "create_lan_scanner", create)


def found() -> DiscoveryReport:
    robot = LanRobot(
        "mini-1", "10.0.0.2", 7000, "_DEDU_mini_robot_control._tcp.local.", {"sn": "ABC"}
    )
    return report(seen_by_type={robot.service_type: 1}, records=(robot,))


class TestLanScan:
    def test_report_lists_counters_and_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, found())
        result = CliRunner().invoke(main, ["lan-scan", "--scan-timeout", "1"])
        assert result.exit_code == 0, result.output
        for text in (
            "types browsed: 2",
            "records seen: 1",
            "resolved: 1",
            "robots: 1",
            "mini-1",
            "7000",
            "sn=ABC",
        ):
            assert text in result.output

    def test_empty_result_is_inconclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report())
        result = CliRunner().invoke(main, ["lan-scan", "--scan-timeout", "1"])
        assert result.exit_code == 0
        assert "robots: 0" in result.output
        assert "do not conclude" in result.output

    def test_unresolved_names_are_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report(seen_by_type={"_a._tcp.local.": 1}, unresolved=("ghost",)))
        result = CliRunner().invoke(main, ["lan-scan", "--scan-timeout", "1"])
        assert "unresolved: ghost" in result.output

    def test_wrong_network_aborts_before_scanning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scanners = Scanners(monkeypatch, found())
        monkeypatch.setattr(cli_program, "create_network_detector", lambda: FakeDetector("other"))
        result = CliRunner().invoke(main, ["lan-scan", "--expect-ssid", "demo_neuron"])
        assert result.exit_code == 1
        assert "false negative" in result.output
        assert scanners.scans == 0

    def test_expected_network_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, found())
        result = CliRunner().invoke(
            main, ["lan-scan", "--expect-ssid", "demo_neuron", "--scan-timeout", "1"]
        )
        assert result.exit_code == 0, result.output
        assert "ssid: demo_neuron (expected: demo_neuron)" in result.output

    def test_no_wifi_only_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, found())
        monkeypatch.setattr(cli_program, "create_network_detector", lambda: FakeDetector(None))
        result = CliRunner().invoke(
            main, ["lan-scan", "--expect-ssid", "demo_neuron", "--scan-timeout", "1"]
        )
        assert result.exit_code == 0, result.output
        assert "no active Wi-Fi detected" in result.output

    def test_interface_is_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scanners = Scanners(monkeypatch, found())
        result = CliRunner().invoke(
            main,
            [
                "lan-scan",
                "--interface",
                "192.168.0.10",
                "--interface",
                "10.0.0.5",
                "--scan-timeout",
                "1",
            ],
        )
        assert result.exit_code == 0, result.output
        assert scanners.interfaces == [("192.168.0.10", "10.0.0.5")]
        assert "interface: 192.168.0.10, 10.0.0.5" in result.output


class TestLanDiag:
    def probe(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[int, ...]]]:
        calls: list[tuple[str, tuple[int, ...]]] = []

        async def fake(host: str, ports: tuple[int, ...]) -> list[PortResult]:
            calls.append((host, ports))
            return [PortResult(p, p == 7000, "open" if p == 7000 else "refused") for p in ports]

        monkeypatch.setattr(cli_program, "probe_ports", fake)
        return calls

    def test_scan_only_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, found())
        calls = self.probe(monkeypatch)
        result = CliRunner().invoke(main, ["lan-diag", "--scan-timeout", "1"])
        assert result.exit_code == 0, result.output
        assert "robots: 1" in result.output
        assert calls == []

    def test_tcp_test_after_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report())
        calls = self.probe(monkeypatch)
        result = CliRunner().invoke(
            main,
            ["lan-diag", "--scan-timeout", "1", "--diag-connect", "--host", "10.0.0.2"],
            input="y\n",
        )
        assert result.exit_code == 0, result.output
        assert calls == [("10.0.0.2", (7000, 8801, 5555))]
        assert "TCP test: 10.0.0.2 ports 7000, 8801, 5555" in result.output
        assert "Only the listed ports were tested" in result.output

    def test_custom_ports_and_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report())
        calls = self.probe(monkeypatch)
        result = CliRunner().invoke(
            main,
            [
                "lan-diag",
                "--scan-timeout",
                "1",
                "--diag-connect",
                "--host",
                "h",
                "--ports",
                "7000, 22",
                "-y",
            ],
        )
        assert result.exit_code == 0, result.output
        assert calls == [("h", (7000, 22))]

    def test_declined_confirmation_probes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report())
        calls = self.probe(monkeypatch)
        result = CliRunner().invoke(
            main,
            ["lan-diag", "--scan-timeout", "1", "--diag-connect", "--host", "h"],
            input="n\n",
        )
        assert result.exit_code == 1
        assert calls == []

    def test_host_is_required_for_the_tcp_test(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scanners = Scanners(monkeypatch, report())
        result = CliRunner().invoke(main, ["lan-diag", "--diag-connect"])
        assert result.exit_code == 2
        assert "--host is required" in result.output
        assert scanners.scans == 0

    @pytest.mark.parametrize("ports", ["abc", "0", "70000", ","])
    def test_invalid_ports(self, monkeypatch: pytest.MonkeyPatch, ports: str) -> None:
        Scanners(monkeypatch, report())
        result = CliRunner().invoke(main, ["lan-diag", "--ports", ports])
        assert result.exit_code == 2
        assert "invalid port list" in result.output

    def test_wrong_network_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Scanners(monkeypatch, report())
        monkeypatch.setattr(cli_program, "create_network_detector", lambda: FakeDetector("x"))
        result = CliRunner().invoke(main, ["lan-diag", "--expect-ssid", "demo_neuron"])
        assert result.exit_code == 1


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

    def test_run_via_lan_prints_the_robot_handshake(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inner = ScriptedTransport()

        class FakeLan(LanTransport):
            def __init__(self) -> None:
                super().__init__("h", 1)
                self._result = parse_lan_handshake_result(
                    LanHandshakeResponse(
                        isSuccess=True,
                        robotSoftVersionName="2.0.1",
                        androidFirmwareVersion="fw-9",
                        btInterfaceVersion=3,
                        sessionId="rs",
                    ).SerializeToString()
                )

            @property
            def handshake_result(self) -> LanHandshakeResult | None:
                return self._result

            async def connect(self) -> None:
                await inner.connect()

            async def close(self) -> None:
                await inner.close()

            async def send(self, frame: bytes) -> None:
                await inner.send(frame)

            async def receive(self) -> bytes:
                return await inner.receive()

        monkeypatch.setattr(cli_program, "create_lan_transport", lambda _host, _port: FakeLan())
        result = invoke("run", *HANDSHAKE, "--via", "lan", "--host", "h", "--port", "7000", "-y")
        assert result.exit_code == 0, result.output
        assert "software=2.0.1" in result.output
        assert "firmware=fw-9" in result.output
        assert "sessionId=rs" in result.output


def test_default_factories() -> None:
    kwargs: dict[str, Any] = {"trailer_size": 4}
    assert isinstance(cli_program.create_ble_transport("AA", **kwargs), BleTransport)
    assert isinstance(cli_program.create_lan_transport("h", 1), LanTransport)
    assert isinstance(cli_program.create_lan_scanner(), ZeroconfLanScanner)


def test_network_detector_factory_uses_the_os_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = FakeDetector("sentinel")
    monkeypatch.setattr(cli_program, "create_detector", lambda: sentinel)
    assert REAL_NETWORK_DETECTOR_FACTORY() is sentinel
