import json

import pytest
from pydantic import ValidationError

from alpha_mini_wifi_encoder import WifiCommand, WifiCredentials
from alpha_mini_wifi_encoder.models import WifiPayload
from alpha_mini_wifi_encoder.serializers import CompactJsonSerializer, GsonCompatSerializer


def make_payload(password: str = "pwd") -> WifiPayload:
    creds = WifiCredentials(ssid="ssid", capabilities="WPA", password=password, dont_ping="0")
    return WifiPayload.from_credentials(creds, WifiCommand.WIFI_INFO_TRANS_FOR_BINDING)


def test_wire_dict_order_and_null_omission() -> None:
    assert list(make_payload().to_wire_dict()) == ["c", "p", "s", "v", "co"]


def test_compact_serializer_does_not_escape_html() -> None:
    text = CompactJsonSerializer().serialize(make_payload("a=b"))
    assert "a=b" in text
    assert json.loads(text)["p"] == "a=b"


def test_gson_serializer_escapes_html_and_stays_valid_json() -> None:
    text = GsonCompatSerializer().serialize(make_payload("a=b\u2028"))
    assert "\\u003d" in text
    assert "\\u2028" in text
    assert json.loads(text)["p"] == "a=b\u2028"


def test_non_ascii_is_emitted_raw() -> None:
    creds = WifiCredentials(ssid="café", capabilities="WPA")
    payload = WifiPayload.from_credentials(creds, WifiCommand.WIFI_INFO_TRANS)
    assert "café" in GsonCompatSerializer().serialize(payload)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ssid": "", "capabilities": "c"},
        {"ssid": "é" * 17, "capabilities": "c"},
        {"ssid": "s", "capabilities": "c", "password": "x" * 65},
        {"ssid": "s", "capabilities": "c", "bssid": "not-a-mac"},
        {"ssid": "s", "capabilities": "c", "dont_ping": "2"},
        {"ssid": "s", "capabilities": "c", "unknown": 1},
    ],
)
def test_invalid_credentials_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WifiCredentials.model_validate(kwargs)
