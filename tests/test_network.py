import json
import subprocess
import sys
from collections.abc import Sequence

import pytest

from alpha_mini_wifi_encoder.network import (
    CommandRunner,
    NetshDetector,
    NetworkDetectionError,
    NmcliDetector,
    Security,
    SystemProfilerDetector,
    UnsupportedSecurityError,
    classify_security,
    create_detector,
    run_command,
)


def fake(output: str) -> CommandRunner:
    def runner(_args: Sequence[str]) -> str:
        return output

    return runner


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("WPA2", Security.WPA2),
        ("WPA1 WPA2", Security.WPA2),
        ("WPA2 WPA3", Security.WPA2),
        ("WPA2-Personal", Security.WPA2),
        ("spairport_security_mode_wpa2_personal", Security.WPA2),
        ("WPA", Security.WPA),
        ("WEP", Security.WEP),
        ("--", Security.OPEN),
        ("Open", Security.OPEN),
        ("Aberta", Security.OPEN),
        ("spairport_security_mode_none", Security.OPEN),
    ],
)
def test_classify_security(raw: str, expected: Security) -> None:
    assert classify_security(raw) is expected


@pytest.mark.parametrize("raw", ["WPA3", "WPA2-Enterprise", "WPA2 802.1X", "", "banana"])
def test_classify_security_unsupported(raw: str) -> None:
    with pytest.raises(UnsupportedSecurityError):
        classify_security(raw)


def test_nmcli_picks_active_row_and_unescapes() -> None:
    out = (
        "no:Other:11\\:11\\:11\\:11\\:11\\:11:WPA2\nyes:My\\:Net:AA\\:BB\\:CC\\:DD\\:EE\\:FF:WPA2\n"
    )
    net = NmcliDetector(fake(out)).detect()
    assert (net.ssid, net.security, net.bssid) == ("My:Net", Security.WPA2, "aa:bb:cc:dd:ee:ff")


@pytest.mark.parametrize("out", ["", "no:X:AA\\:BB\\:CC\\:DD\\:EE\\:FF:WPA2\n", "yes::AA:WPA2\n"])
def test_nmcli_not_connected(out: str) -> None:
    with pytest.raises(NetworkDetectionError, match="not connected"):
        NmcliDetector(fake(out)).detect()


NETSH_EN = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : MyWiFi
    BSSID                  : AA:BB:CC:DD:EE:FF
    Network type           : Infrastructure
    Radio type             : 802.11ac
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
"""
NETSH_PT = NETSH_EN.replace("Authentication", "Autenticação").replace("WPA2-Personal", "Aberta")


def test_netsh_english() -> None:
    net = NetshDetector(fake(NETSH_EN)).detect()
    assert (net.ssid, net.security, net.bssid) == ("MyWiFi", Security.WPA2, "aa:bb:cc:dd:ee:ff")


def test_netsh_portuguese_open() -> None:
    assert NetshDetector(fake(NETSH_PT)).detect().security is Security.OPEN


def test_netsh_not_connected_and_unknown_security() -> None:
    with pytest.raises(NetworkDetectionError, match="not connected"):
        NetshDetector(fake("    State : disconnected\n")).detect()
    with pytest.raises(UnsupportedSecurityError):
        NetshDetector(fake("    SSID : X\n")).detect()


def profiler(name: str = "MyWiFi", mode: str = "spairport_security_mode_wpa2_personal") -> str:
    current = {"_name": name, "spairport_security_mode": mode}
    iface = {"spairport_current_network_information": current}
    return json.dumps({"SPAirPortDataType": [{"spairport_airport_interfaces": [{}, iface]}]})


def test_system_profiler_ok() -> None:
    net = SystemProfilerDetector(fake(profiler())).detect()
    assert (net.ssid, net.security, net.bssid) == ("MyWiFi", Security.WPA2, None)


def test_system_profiler_errors() -> None:
    with pytest.raises(NetworkDetectionError, match="redacted"):
        SystemProfilerDetector(fake(profiler("<redacted>"))).detect()
    with pytest.raises(NetworkDetectionError, match="unexpected"):
        SystemProfilerDetector(fake("not json")).detect()
    empty = json.dumps({"SPAirPortDataType": [{}]})
    with pytest.raises(NetworkDetectionError, match="not connected"):
        SystemProfilerDetector(fake(empty)).detect()


def test_create_detector_by_system() -> None:
    assert isinstance(create_detector("Linux"), NmcliDetector)
    assert isinstance(create_detector("Windows"), NetshDetector)
    assert isinstance(create_detector("Darwin"), SystemProfilerDetector)
    assert create_detector(None) is not None
    with pytest.raises(NetworkDetectionError, match="not supported"):
        create_detector("Plan9")


def test_run_command_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_command([sys.executable, "-c", "print('hi')"]).strip() == "hi"
    with pytest.raises(NetworkDetectionError, match="not found"):
        run_command(["definitely-not-a-command-xyz"])
    with pytest.raises(NetworkDetectionError, match="failed"):
        run_command([sys.executable, "-c", "import sys; sys.exit(3)"])

    def boom(*_a: object, **_k: object) -> None:
        timeout = subprocess.TimeoutExpired("x", 1)
        raise timeout

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(NetworkDetectionError, match="timed out"):
        run_command(["x"])


def test_to_credentials_bssid_toggle() -> None:
    net = NmcliDetector(fake("yes:S:AA\\:BB\\:CC\\:DD\\:EE\\:FF:WPA2\n")).detect()
    assert net.to_credentials(password="p").bssid is None
    assert net.to_credentials(password="p", include_bssid=True).bssid == "aa:bb:cc:dd:ee:ff"
