import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alpha_mini_wifi_encoder import (
    PayloadTooLargeError,
    WifiCommand,
    WifiCredentials,
    WifiProvisioningEncoder,
)
from alpha_mini_wifi_encoder.layers import FrameLayer
from tests.helpers import decode_json_text, reassemble

EXPECTED_JSON = (
    '{"b":"aa:bb:cc:dd:ee:ff","c":"[WPA-PSK-CCMP][WPA2-PSK-CCMP][ESS]",'
    '"p":"password123","s":"MyWiFi","v":"0","co":21}'
)
EXPECTED_CHUNKS = [
    "fb bf 01 00 86 0a 06 08 20 10 01 20 01 12 76 0a 12 34 00 01",
    "71 7b 22 62 22 3a 22 61 61 3a 62 62 3a 63 63 3a 12 34 00 02",
    "64 64 3a 65 65 3a 66 66 22 2c 22 63 22 3a 22 5b 12 34 00 03",
    "57 50 41 2d 50 53 4b 2d 43 43 4d 50 5d 5b 57 50 12 34 00 04",
    "41 32 2d 50 53 4b 2d 43 43 4d 50 5d 5b 45 53 53 12 34 00 05",
    "5d 22 2c 22 70 22 3a 22 70 61 73 73 77 6f 72 64 12 34 00 06",
    "31 32 33 22 2c 22 73 22 3a 22 4d 79 57 69 46 69 12 34 00 07",
    "22 2c 22 76 22 3a 22 30 22 2c 22 63 6f 22 3a 32 12 34 00 08",
    "31 7d 10 a8 01 01 ed 12 34 00 09",
]


def test_worked_example_matches_report(
    encoder: WifiProvisioningEncoder,
    credentials: WifiCredentials,
) -> None:
    result = encoder.encode(credentials)

    assert result.json_text == EXPECTED_JSON
    assert result.frame[:15].hex(" ") == "fb bf 01 00 86 0a 06 08 20 10 01 20 01 12 76"
    assert len(result.frame) == 135
    assert [chunk.hex(" ") for chunk in result.chunks] == EXPECTED_CHUNKS
    assert reassemble(result.chunks) == result.frame


def test_frame_length_field_is_total_minus_one(
    encoder: WifiProvisioningEncoder,
    credentials: WifiCredentials,
) -> None:
    frame = encoder.encode(credentials).frame
    assert int.from_bytes(frame[3:5], "big") == len(frame) - 1 == 134


def test_header_bytes_for_first_request(
    encoder: WifiProvisioningEncoder,
    credentials: WifiCredentials,
) -> None:
    result = encoder.encode(credentials)
    assert bytes.fromhex("0a06082010012001") == result.layers[1].data[:8]
    assert result.layers[0].data.endswith(bytes.fromhex("10a801"))


def test_change_wifi_and_omitted_fields(encoder: WifiProvisioningEncoder) -> None:
    creds = WifiCredentials(
        ssid="ssid",
        capabilities="WPA",
        bssid="aa:bb:cc:dd:ee:ff",
    )
    result = encoder.encode(creds, command=WifiCommand.WIFI_INFO_TRANS)
    assert result.json_text == '{"b":"aa:bb:cc:dd:ee:ff","c":"WPA","p":"","s":"ssid","co":2}'


def test_send_serial_uses_multibyte_varint(
    encoder: WifiProvisioningEncoder,
    credentials: WifiCredentials,
) -> None:
    frame = encoder.encode(credentials, send_serial=300).frame
    _, serial, command_id, version = decode_json_text(frame)
    assert (serial, command_id, version) == (300, 32, 168)


def test_zero_send_serial_is_omitted_by_proto3(
    encoder: WifiProvisioningEncoder,
    credentials: WifiCredentials,
) -> None:
    result = encoder.encode(credentials, send_serial=0)
    # sendSerial=0 is a proto3 default: tag 0x10 is absent from the header.
    assert result.layers[1].data[:8] == bytes.fromhex("0a0408202001") + result.layers[1].data[6:8]
    assert result.layers[1].data[2:6] == bytes.fromhex("08202001")


def test_gson_html_escaping_is_applied(encoder: WifiProvisioningEncoder) -> None:
    creds = WifiCredentials(ssid="s", capabilities="c", password="a=b'c&d<e>f")
    text = encoder.encode(creds).json_text
    assert "a\\u003db\\u0027c\\u0026d\\u003ce\\u003ef" in text


def test_oversized_payload_is_rejected() -> None:
    with pytest.raises(PayloadTooLargeError):
        FrameLayer().encode(b"x" * 0x10000)


@settings(max_examples=75, deadline=None)
@given(
    ssid=st.text(min_size=1, max_size=8).filter(lambda s: len(s.encode()) <= 32),
    password=st.text(max_size=16).filter(lambda s: len(s.encode()) <= 64),
    serial=st.integers(min_value=0, max_value=2**40),
    change=st.booleans(),
)
def test_round_trip(ssid: str, password: str, serial: int, change: bool) -> None:
    encoder = WifiProvisioningEncoder.with_defaults()
    creds = WifiCredentials(ssid=ssid, capabilities="[ESS]", password=password)
    command = WifiCommand.WIFI_INFO_TRANS if change else WifiCommand.WIFI_INFO_TRANS_FOR_BINDING
    result = encoder.encode(creds, command=command, send_serial=serial)

    text, decoded_serial, command_id, version = decode_json_text(reassemble(result.chunks))

    assert text == result.json_text
    assert (decoded_serial, command_id, version) == (serial, 32, 168)
    assert all(len(chunk) <= 20 for chunk in result.chunks)
