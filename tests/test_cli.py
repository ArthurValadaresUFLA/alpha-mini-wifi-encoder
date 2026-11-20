import json
import runpy

import pytest
from click.testing import CliRunner

from alpha_mini_wifi_encoder.cli import encode, main
from alpha_mini_wifi_encoder.network import DetectedNetwork, NetworkDetectionError, Security

BASE = ["--cap", "[WPA-PSK-CCMP][WPA2-PSK-CCMP][ESS]", "--ssid", "MyWiFi", "--pwd", "password123"]


def test_json_output_matches_report() -> None:
    args = [*BASE, "--bssid", "aa:bb:cc:dd:ee:ff", "--dont-ping", "0", "--output", "json"]
    result = CliRunner().invoke(encode, args)

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["frame"].startswith("fbbf0100860a06082010012001127")
    assert len(data["chunks"]) == 9
    assert data["chunks"][0] == "fbbf0100860a06082010012001127" + "60a12340001"


def test_pretty_output_with_layers() -> None:
    result = CliRunner().invoke(encode, [*BASE, "--layers", "--change-wifi", "--randomize"])
    assert result.exit_code == 0, result.output
    assert "GATT writes" in result.output
    assert "Layer 4" in result.output
    assert "nothing was sent" in result.output


def test_compact_json_style() -> None:
    result = CliRunner().invoke(
        encode,
        [
            "--cap",
            "c",
            "--ssid",
            "s",
            "--pwd",
            "a=b",
            "--json-style",
            "compact",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "a=b" in json.loads(result.output)["json"]


def test_validation_error_is_reported_as_usage_error() -> None:
    result = CliRunner().invoke(encode, ["--cap", "c", "--ssid", "s", "--bssid", "oops"])
    assert result.exit_code == 2
    assert "BSSID" in result.output


def test_encoder_error_is_reported() -> None:
    result = CliRunner().invoke(encode, ["--cap", "c" * 70000, "--ssid", "s"])
    assert result.exit_code == 1
    assert "exceeds" in result.output


def test_password_from_environment() -> None:
    result = CliRunner().invoke(
        encode,
        ["--cap", "c", "--ssid", "s", "--output", "json"],
        env={"ALPHA_MINI_WIFI_PASSWORD": "fromenv"},
    )
    assert "fromenv" in json.loads(result.output)["json"]


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert "0.1.0" in result.output


def test_module_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["alpha-mini-wifi-encode", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("alpha_mini_wifi_encoder", run_name="__main__")
    assert exit_info.value.code == 0


class FakeDetector:
    def __init__(self, security: Security = Security.WPA2) -> None:
        self._net = DetectedNetwork("LaptopNet", security, "aa:bb:cc:dd:ee:ff")

    def detect(self) -> DetectedNetwork:
        return self._net


def use_detector(monkeypatch: pytest.MonkeyPatch, detector: object) -> None:
    monkeypatch.setattr("alpha_mini_wifi_encoder.cli.create_detector", lambda: detector)


def test_current_network_prompts_for_password(monkeypatch: pytest.MonkeyPatch) -> None:
    use_detector(monkeypatch, FakeDetector())
    result = CliRunner().invoke(encode, ["--current-network", "--output", "json"], input="secret\n")
    assert result.exit_code == 0, result.output
    text = json.loads(result.stdout)["json"]
    assert text == '{"c":"WPA2","p":"secret","s":"LaptopNet","co":21}'


def test_current_network_with_env_password_and_bssid(monkeypatch: pytest.MonkeyPatch) -> None:
    use_detector(monkeypatch, FakeDetector())
    result = CliRunner().invoke(
        encode,
        ["--current-network", "--include-bssid", "--output", "json"],
        env={"ALPHA_MINI_WIFI_PASSWORD": "pw"},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["json"].startswith('{"b":"aa:bb:cc:dd:ee:ff","c":"WPA2"')


def test_current_network_open_needs_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    use_detector(monkeypatch, FakeDetector(Security.OPEN))
    result = CliRunner().invoke(encode, ["--current-network", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert '"p":""' in json.loads(result.stdout)["json"]


def test_explicit_options_override_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    use_detector(monkeypatch, FakeDetector())
    args = [
        "--current-network",
        "--ssid",
        "Other",
        "--cap",
        "WPA",
        "--pwd",
        "x",
        "--output",
        "json",
    ]
    result = CliRunner().invoke(encode, args)
    assert json.loads(result.stdout)["json"] == '{"c":"WPA","p":"x","s":"Other","co":21}'


def test_current_network_detection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing() -> object:
        message = "no wifi"
        raise NetworkDetectionError(message)

    monkeypatch.setattr("alpha_mini_wifi_encoder.cli.create_detector", failing)
    result = CliRunner().invoke(encode, ["--current-network"])
    assert result.exit_code == 1
    assert "no wifi" in result.output


def test_ssid_and_cap_required_without_current_network() -> None:
    result = CliRunner().invoke(encode, ["--ssid", "only"])
    assert result.exit_code == 2
    assert "--current-network" in result.output
