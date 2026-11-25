import pytest

from alpha_mini_wifi_encoder.codemao import (
    CodeMaoCodec,
    CodeMaoRequest,
    control_program_request,
    handshake_request,
    parse_handshake_result,
    parse_result,
    parse_revert_origin_result,
    revert_origin_request,
)
from alpha_mini_wifi_encoder.codemao_messages import HandShakeResponse, RevertOriginResponse
from alpha_mini_wifi_encoder.constants import CodeMaoCommand
from alpha_mini_wifi_encoder.errors import ProtocolError
from alpha_mini_wifi_encoder.frames import FrameStreamDecoder
from alpha_mini_wifi_encoder.layers import unpack_frame
from tests.codemao_helpers import envelope, frame_of, result_body


def test_handshake_golden_vector() -> None:
    request = handshake_request(user_id="u", nickname="n", programme_version=3)
    assert request.command_id == CodeMaoCommand.HANDSHAKE
    assert int(request.command_id) == 29
    assert request.body == bytes.fromhex("0a016e12017518 03".replace(" ", ""))
    frame = CodeMaoCodec.encode(request, send_serial=1)
    header = bytes.fromhex("081d10012001")
    payload = b"\x0a\x06" + header + b"\x12\x08" + request.body
    assert frame == b"\xfb\xbf\x01" + (len(payload) + 6).to_bytes(2, "big") + payload + b"\x01\xed"


def test_run_stop_and_revert_bodies() -> None:
    assert control_program_request(start=True).body == b"\x08\x01"
    assert control_program_request(start=True).command_id == 33
    assert control_program_request(start=False).body == b""
    assert revert_origin_request() == CodeMaoRequest(28, b"")


def test_decode_round_trip() -> None:
    frame = CodeMaoCodec.encode(handshake_request("u", "n", 3), send_serial=9)
    reply = CodeMaoCodec.decode(unpack_frame(frame))
    assert (reply.command_id, reply.send_serial, reply.response_serial, reply.version) == (
        29,
        9,
        0,
        1,
    )
    assert reply.msg_type == 0
    assert reply.body.startswith(b"\x0a\x01n")


def test_decode_rejects_garbage() -> None:
    with pytest.raises(ProtocolError, match="envelope"):
        CodeMaoCodec.decode(b"\xff\xff\xff")


def test_parse_results() -> None:
    ok = parse_result(result_body(RevertOriginResponse, code=0))
    assert (ok.is_success, ok.result_code, ok.message) == (True, 0, "")
    revert = parse_revert_origin_result(result_body(RevertOriginResponse, success=False, code=4))
    assert (revert.is_success, revert.result_code) == (False, 4)
    body = HandShakeResponse(
        isSuccess=True,
        resultCode=0,
        returnMsg="hi",
        robotProgrammeVersion=7,
        nickName="r",
        userId="9",
    ).SerializeToString()
    handshake = parse_handshake_result(body)
    assert (handshake.robot_programme_version, handshake.nickname, handshake.user_id) == (
        7,
        "r",
        "9",
    )
    assert handshake.message == "hi"


@pytest.mark.parametrize(
    "parser", [parse_result, parse_revert_origin_result, parse_handshake_result]
)
def test_parsers_reject_garbage(parser: object) -> None:
    with pytest.raises(ProtocolError):
        parser(b"\xff\xff\xff")  # type: ignore[operator]


class TestFrameStreamDecoder:
    def test_single_frame(self) -> None:
        decoder = FrameStreamDecoder()
        assert decoder.feed(frame_of(b"abc")) == [b"abc"]
        assert decoder.pending == 0

    def test_byte_by_byte_and_two_frames(self) -> None:
        stream = frame_of(b"one") + frame_of(b"two")
        decoder = FrameStreamDecoder()
        out = [c for byte in stream for c in decoder.feed(bytes([byte]))]
        assert out == [b"one", b"two"]

    def test_garbage_before_and_between(self) -> None:
        decoder = FrameStreamDecoder()
        stream = b"\x00\xfb\x99junk" + frame_of(b"ok") + b"\xfb" + frame_of(b"ok2")
        assert decoder.feed(stream) == [b"ok", b"ok2"]

    def test_invalid_frame_is_skipped(self) -> None:
        bad = bytearray(frame_of(b"bad"))
        bad[-1] = 0x00  # break the terminator
        decoder = FrameStreamDecoder()
        assert decoder.feed(bytes(bad) + frame_of(b"good")) == [b"good"]

    def test_partial_frame_waits(self) -> None:
        frame = frame_of(b"xyz")
        decoder = FrameStreamDecoder()
        assert decoder.feed(frame[:4]) == []
        assert decoder.pending == 4
        assert decoder.feed(frame[4:-1]) == []
        assert decoder.feed(frame[-1:]) == [b"xyz"]

    def test_no_marker_keeps_only_a_possible_partial_magic(self) -> None:
        decoder = FrameStreamDecoder()
        assert decoder.feed(b"nothing here\xfb") == []
        assert decoder.pending == 1


def test_envelope_helper_matches_codec() -> None:
    reply = CodeMaoCodec.decode(envelope(33, b"\x01", response_serial=4, send_serial=2))
    assert (reply.command_id, reply.send_serial, reply.response_serial, reply.body) == (
        33,
        2,
        4,
        b"\x01",
    )
