import sys
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
import questionary

from alpha_mini_wifi_encoder import interactive
from alpha_mini_wifi_encoder.ble import BleDevice
from alpha_mini_wifi_encoder.errors import EncoderError, UserAbortedError
from alpha_mini_wifi_encoder.interactive import (
    NetworkChoice,
    QuestionaryPrompter,
    ask_network_password,
    select_device,
    select_network,
)
from alpha_mini_wifi_encoder.network import (
    DetectedNetwork,
    NetworkDetectionError,
    Security,
)
from alpha_mini_wifi_encoder.saved_networks import (
    NetshSavedNetworks,
    NetworksetupSavedNetworks,
    NmcliSavedNetworks,
    SavedNetwork,
    create_saved_networks,
)
from tests.fakes import FakeDetector, FakePrompter, FakeSaved

CURRENT = DetectedNetwork("LaptopNet", Security.WPA2, "aa:bb:cc:dd:ee:ff")


# ------------------------------------------------------------ saved networks
def nmcli_runner(args: Sequence[str]) -> str:
    if "-g" in args:
        name = args[-1]
        profiles = {
            "Home": "HomeSSID\nwpa-psk\n",
            "Work:Net": "Work\\:Net\n\n",
            "Corp": "Corp\nwpa-eap\n",
            "Wep": "WepNet\nnone\n",
            "Sae": "SaeNet\nsae\n",
            "Dup": "HomeSSID\nwpa-psk\n",
            "NoSsid": "\nwpa-psk\n",
        }
        if name == "Broken":
            message = "boom"
            raise NetworkDetectionError(message)
        return profiles[name]
    return (
        "Home:802-11-wireless\nWired:802-3-ethernet\nWork\\:Net:wifi\nCorp:802-11-wireless\n"
        "Broken:802-11-wireless\nWep:wifi\nSae:wifi\nDup:wifi\nNoSsid:wifi\nbad-line\n"
    )


def test_nmcli_saved_networks() -> None:
    networks = NmcliSavedNetworks(nmcli_runner).list_networks()
    assert {n.ssid: n.security for n in networks} == {
        "HomeSSID": Security.WPA2,
        "NoSsid": Security.WPA2,
        "SaeNet": None,
        "WepNet": Security.WEP,
        "Work:Net": Security.OPEN,
    }
    assert [n.ssid for n in networks] == sorted((n.ssid for n in networks), key=str.lower)


def test_netsh_saved_networks() -> None:
    listing = (
        "Profiles on interface Wi-Fi:\n\nGroup policy profiles (read only)\n---------\n    <None>\n\n"
        "User profiles\n-------------\n    All User Profile     : HomeNet\n"
        "    Perfil de Todos os Usuários : CafeNet\n    All User Profile     : Wpa3Net\n"
        "    All User Profile     : Broken\n    All User Profile     : NoAuth\n"
    )

    def runner(args: Sequence[str]) -> str:
        if args[3] == "profiles":
            return listing
        name = args[-1].removeprefix("name=")
        if name == "Broken":
            message = "boom"
            raise NetworkDetectionError(message)
        return {
            "HomeNet": "    Authentication         : WPA2-Personal\n",
            "CafeNet": "    Authentication : Open\n",
            "Wpa3Net": "    Authentication : WPA3-Personal\n",
            "NoAuth": "    Cipher : CCMP\n",
        }[name]

    got = {n.ssid: n.security for n in NetshSavedNetworks(runner).list_networks()}
    assert got == {
        "Broken": None,
        "CafeNet": Security.OPEN,
        "HomeNet": Security.WPA2,
        "NoAuth": None,
        "Wpa3Net": None,
    }


def test_networksetup_saved_networks() -> None:
    def runner(_args: Sequence[str]) -> str:
        return "Preferred networks on en0:\n\tHome\n\tCafe\n\n"

    assert NetworksetupSavedNetworks(runner).list_networks() == [
        SavedNetwork("Cafe"),
        SavedNetwork("Home"),
    ]


def test_create_saved_networks() -> None:
    assert isinstance(create_saved_networks("Linux"), NmcliSavedNetworks)
    assert isinstance(create_saved_networks("Windows"), NetshSavedNetworks)
    assert isinstance(create_saved_networks("Darwin"), NetworksetupSavedNetworks)
    assert create_saved_networks() is not None
    with pytest.raises(NetworkDetectionError, match="not supported"):
        create_saved_networks("Plan9")


# ------------------------------------------------------------------- menus
def test_select_device_with_rescan_and_cancel() -> None:
    first, second = BleDevice("A", "One"), BleDevice("B", "Two")
    scans = iter([[], [first, second]])
    prompter = FakePrompter("Rescan", "Two")
    assert select_device(prompter, lambda: next(scans)) is second
    with pytest.raises(UserAbortedError):
        select_device(FakePrompter("Cancel"), lambda: [first])


def test_select_network_saved_with_security() -> None:
    prompter = FakePrompter("Home")
    saved = FakeSaved(
        [SavedNetwork("Home", Security.WPA), SavedNetwork("LaptopNet", Security.WPA2)]
    )
    choice = select_network(prompter, FakeDetector(CURRENT), saved)
    assert choice == NetworkChoice("Home", Security.WPA)
    menu = prompter.menus[0]
    assert menu[0].startswith("Current network: LaptopNet")
    assert not any(label.startswith("LaptopNet (") for label in menu)
    assert menu[-1] == "Enter another network..."


def test_select_network_current_keeps_bssid() -> None:
    choice = select_network(FakePrompter("Current"), FakeDetector(CURRENT), FakeSaved([]))
    assert choice == NetworkChoice("LaptopNet", Security.WPA2, "aa:bb:cc:dd:ee:ff")


def test_select_network_unknown_security_asks() -> None:
    saved = FakeSaved([SavedNetwork("Legacy")])
    choice = select_network(FakePrompter("Legacy", "WEP"), FakeDetector(None), saved)
    assert choice == NetworkChoice("Legacy", Security.WEP)


def test_select_network_other_and_missing_providers() -> None:
    choice = select_network(FakePrompter("another", "Cafe", "Open"), None, None)
    assert choice == NetworkChoice("Cafe", Security.OPEN)
    failing = FakeSaved(NetworkDetectionError("x"))
    assert (
        select_network(FakePrompter("another", "N", "WPA2"), FakeDetector(None), failing).ssid
        == "N"
    )


def test_ask_network_password() -> None:
    prompter = FakePrompter("secret")
    assert ask_network_password(prompter, NetworkChoice("S", Security.OPEN)) == ""
    assert ask_network_password(prompter, NetworkChoice("S", Security.WPA2)) == "secret"
    assert "not stored" in prompter.password_messages[0]


# ----------------------------------------------------- questionary adapter
class Tty:
    def isatty(self) -> bool:
        return True


class Answer:
    def __init__(self, value: Any) -> None:
        self.value = value

    def ask(self) -> Any:
        return self.value


def test_questionary_prompter_requires_terminal() -> None:
    with pytest.raises(EncoderError, match="terminal"):
        QuestionaryPrompter()


def test_questionary_prompter_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(interactive, "sys", SimpleNamespace(stdin=Tty(), stdout=Tty()))
    answers: dict[str, Any] = {"select": 1, "text": "  hi ", "password": "pw", "confirm": True}
    for name in answers:
        monkeypatch.setattr(
            questionary,
            name,
            lambda *_a, _n=name, **_k: Answer(answers[_n]),
        )
    prompter = QuestionaryPrompter()
    assert prompter.select("m", [("a", 1), ("b", 2)]) == 2
    assert prompter.text("m") == "hi"
    assert prompter.password("m") == "pw"
    assert prompter.confirm("m") is True
    for name in answers:
        answers[name] = None
        monkeypatch.setattr(questionary, name, lambda *_a, **_k: Answer(None))
    for call in (
        lambda: prompter.select("m", [("a", 1)]),
        lambda: prompter.text("m"),
        lambda: prompter.password("m"),
        lambda: prompter.confirm("m"),
    ):
        with pytest.raises(UserAbortedError):
            call()
    assert sys is not None
