import json
import runpy

import pytest
from click.testing import CliRunner

from alpha_mini_wifi_encoder.cli import main

BASE = ["--cap", "[WPA-PSK-CCMP][WPA2-PSK-CCMP][ESS]", "--ssid", "MyWiFi", "--pwd", "password123"]


def test_json_output_matches_report() -> None:
    args = [*BASE, "--bssid", "aa:bb:cc:dd:ee:ff", "--dont-ping", "0", "--output", "json"]
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["frame"].startswith("fbbf0100860a06082010012001127")
    assert len(data["chunks"]) == 9
    assert data["chunks"][0] == "fbbf0100860a06082010012001127" + "60a12340001"


def test_pretty_output_with_layers() -> None:
    result = CliRunner().invoke(main, [*BASE, "--layers", "--change-wifi", "--randomize"])
    assert result.exit_code == 0, result.output
    assert "GATT writes" in result.output
    assert "Layer 4" in result.output
    assert "nothing was sent" in result.output


def test_compact_json_style() -> None:
    result = CliRunner().invoke(
        main,
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
    result = CliRunner().invoke(main, ["--cap", "c", "--ssid", "s", "--bssid", "oops"])
    assert result.exit_code == 2
    assert "BSSID" in result.output


def test_encoder_error_is_reported() -> None:
    result = CliRunner().invoke(main, ["--cap", "c" * 70000, "--ssid", "s"])
    assert result.exit_code == 1
    assert "exceeds" in result.output


def test_password_from_environment() -> None:
    result = CliRunner().invoke(
        main,
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
