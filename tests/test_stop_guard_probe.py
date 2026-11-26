import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from alpha_mini_wifi_encoder.codemao import (
    CodeMaoCodec,
    CodeMaoRequest,
    LanHandshakeResult,
    control_program_request,
    handshake_request,
    revert_origin_request,
)
from alpha_mini_wifi_encoder.constants import LAN_HEARTBEAT_PAYLOADS
from alpha_mini_wifi_encoder.layers import FrameLayer, LanFrameLayer, unpack_frame
from alpha_mini_wifi_encoder.session import CodeMaoSession
from alpha_mini_wifi_encoder.stop_guard import StopConditionError, StopGuard
from tests.codemao_helpers import ScriptedTransport


def result(**overrides: object) -> LanHandshakeResult:
    values: dict[str, object] = {
        "is_success": True,
        "result_code": 0,
        "error_code": 0,
        "session_id": "s",
        "sys_lan": "en",
        "sys_country": "US",
        "robot_soft_version_name": "1",
        "bt_interface_version": 3,
        "android_firmware_version": "fw",
    }
    return LanHandshakeResult(**{**values, **overrides})  # type: ignore[arg-type]


class TestStopGuard:
    @pytest.mark.parametrize(
        "request_",
        [
            handshake_request("u", "n", 1),
            control_program_request(start=True),
            revert_origin_request(),
        ],
    )
    def test_known_commands_are_allowed(self, request_: CodeMaoRequest) -> None:
        StopGuard().check_outgoing(request_)

    @pytest.mark.parametrize("command_id", [1, 316, 7, 18001, 999])
    def test_everything_else_is_refused(self, command_id: int) -> None:
        with pytest.raises(StopConditionError, match="allow-list"):
            StopGuard().check_outgoing(CodeMaoRequest(command_id, b""))

    def test_lan_handshake_command_is_allowed(self) -> None:
        StopGuard().check_outgoing(CodeMaoRequest(0, b""))

    def test_good_handshake_passes(self) -> None:
        StopGuard().check_handshake(result())
        StopGuard(expected_interface_version=3).check_handshake(result())

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"is_success": False, "result_code": 7}, "resultCode=7"),
            ({"error_code": 2}, "errorCode=2"),
            ({"bt_interface_version": 4}, "expected 3"),
        ],
    )
    def test_bad_handshake_stops(self, overrides: dict[str, object], message: str) -> None:
        with pytest.raises(StopConditionError, match=message):
            StopGuard(expected_interface_version=3).check_handshake(result(**overrides))

    def test_session_refuses_forbidden_commands_before_sending(self) -> None:
        async def scenario() -> ScriptedTransport:
            transport = ScriptedTransport()
            async with CodeMaoSession(transport) as session:
                with pytest.raises(StopConditionError):
                    await session.request(CodeMaoRequest(316, b"\x08\x01"))
            return transport

        assert asyncio.run(scenario()).sent == []


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

