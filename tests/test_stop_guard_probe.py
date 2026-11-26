import pytest

from alpha_mini_wifi_encoder.codemao import CodeMaoCodec, handshake_request
from alpha_mini_wifi_encoder.constants import LAN_HEARTBEAT_PAYLOADS
from alpha_mini_wifi_encoder.layers import FrameLayer, LanFrameLayer, unpack_frame
class TestLanFraming:
    def test_checksum_is_length_plus_two(self) -> None:
        frame = LanFrameLayer().encode(b"\x01")
        assert frame == bytes.fromhex("fbbf0100070109ed")
        assert unpack_frame(frame) == b"\x01"

    def test_ble_tail_is_unchanged(self) -> None:
        assert FrameLayer().encode(b"\x01") == bytes.fromhex("fbbf0100070101ed")

    def test_checksum_wraps_at_one_byte(self) -> None:
        payload = bytes(250)
        frame = LanFrameLayer().encode(payload)
        length = int.from_bytes(frame[3:5], "big")
        assert frame[-2] == (length + 2) & 0xFF
        assert unpack_frame(frame) == payload

    def test_codecs_differ_only_in_the_tail(self) -> None:
        request = handshake_request("u", "n", 1)
        ble = CodeMaoCodec().encode(request, 1)
        lan = CodeMaoCodec.for_lan().encode(request, 1)
        assert ble[:-2] == lan[:-2]
        assert ble[-2:] == b"\x01\xed"
        assert lan[-1] == 0xED
        assert lan[-2] == (int.from_bytes(lan[3:5], "big") + 2) & 0xFF

    def test_heartbeat_payloads(self) -> None:
        assert {b"\x00", b"\x01"} == LAN_HEARTBEAT_PAYLOADS
