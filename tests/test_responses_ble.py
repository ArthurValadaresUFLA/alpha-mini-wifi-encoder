import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from bleak import BleakScanner
from bleak.exc import BleakError

from alpha_mini_wifi_encoder import ble
from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.constants import NOTIFY_CHARACTERISTIC_UUID, SERVICE_UUID
from alpha_mini_wifi_encoder.errors import BleError, FrameError
from alpha_mini_wifi_encoder.ids import FixedRandomId
from alpha_mini_wifi_encoder.layers import FrameLayer, MessageLayer, unpack_frame
from alpha_mini_wifi_encoder.protobuf_messages import BindOrSwitchWifiResponse
from alpha_mini_wifi_encoder.responses import WifiResponse, decode_notifications


def response_frame(json_text: str) -> bytes:
    body = BindOrSwitchWifiResponse(data=json_text).SerializeToString()
    return FrameLayer().encode(MessageLayer().encode(body))


def notifications_for(json_text: str) -> tuple[bytes, ...]:
    return TrailerChunker(FixedRandomId()).split(response_frame(json_text))


def test_decode_fragments_with_trailer() -> None:
    [response] = decode_notifications(notifications_for('{"co":102}'))
    assert response.command == 102
    assert not response.is_error
    assert response.describe() == "Wi-Fi information accepted (co=102)"


def test_decode_raw_frame_and_leading_garbage() -> None:
    [response] = decode_notifications([b"\x00\x01" + response_frame('{"co":110}')])
    assert response.command == 110


def test_decode_two_frames() -> None:
    stream = response_frame('{"co":102}') + response_frame('{"co":-1,"ec":5}')
    responses = decode_notifications([stream])
    assert [r.command for r in responses] == [102, -1]
    assert responses[1].is_error
    assert "ec=5" in responses[1].describe()


@pytest.mark.parametrize(
    "notifications",
    [
        [],
        [b"garbage"],
        [b"\xfb\xbf\x01\x00"],
        [response_frame("not json")],
        [response_frame("[1]")],
    ],
)
def test_decode_rejects_invalid(notifications: list[bytes]) -> None:
    assert decode_notifications(notifications) == ()


def test_decode_skips_bad_candidate_then_finds_valid() -> None:
    bad = b"\xfb\xbf\x01\x00\x20" + b"\x00" * 5
    assert len(decode_notifications([bad + response_frame('{"co":103}')])) == 1


def test_describe_unknown_and_missing() -> None:
    assert WifiResponse("{}", 999, None).describe() == "Unknown response (co=999)"
    assert WifiResponse("{}", None, None).describe() == "Unrecognised response"
    parsed = decode_notifications([response_frame('{"co":"x","ec":"y"}')])
    assert parsed[0].command is None
    assert parsed[0].error_code is None


@pytest.mark.parametrize(
    "frame",
    [b"", b"\x00" * 9, b"\xfb\xbf\x02\x00\x08\x00\x01\xed", b"\xfb\xbf\x01\x00\x09\x00\x01\xed"],
)
def test_unpack_frame_errors(frame: bytes) -> None:
    with pytest.raises(FrameError):
        unpack_frame(frame)


# ------------------------------------------------------------------ BLE I/O
def make_found(*items: tuple[str, str | None, int, list[str]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for address, name, rssi, uuids in items:
        device = SimpleNamespace(address=address, name=name)
        adv = SimpleNamespace(local_name=name, rssi=rssi, service_uuids=uuids)
        found[address] = (device, adv)
    return found


def test_scanner_filters_sorts_and_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    found = make_found(
        ("A", "Far", -90, []),
        ("B", "Near", -40, []),
        ("C", "Robot", -80, [SERVICE_UUID.upper()]),
        ("D", None, -30, []),
    )

    async def discover(**_kwargs: Any) -> dict[str, Any]:
        return found

    monkeypatch.setattr(BleakScanner, "discover", discover)
    devices = asyncio.run(ble.BleakDeviceScanner().scan(1))
    assert [d.address for d in devices] == ["C", "B", "A"]
    assert devices[0].advertises_service
    assert "[Alpha Mini service]" in devices[0].label
    with_unnamed = asyncio.run(ble.BleakDeviceScanner(include_unnamed=True).scan(1))
    assert len(with_unnamed) == 4
    assert "(unnamed)" in with_unnamed[1].label or any("(unnamed)" in d.label for d in with_unnamed)
    assert "? dBm" in ble.BleDevice("X", "n").label


def test_scanner_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(**_kwargs: Any) -> dict[str, Any]:
        message = "no adapter"
        raise BleakError(message)

    monkeypatch.setattr(BleakScanner, "discover", discover)
    with pytest.raises(BleError, match="scan failed"):
        asyncio.run(ble.BleakDeviceScanner().scan(1))


class FakeClient:
    instances: list["FakeClient"] = []
    notify_error = False
    write_error = False

    def __init__(self, address: str, timeout: float = 0) -> None:
        self.address = address
        self.writes: list[tuple[str, bytes, Any]] = []
        self.callback: Any = None
        FakeClient.instances.append(self)

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def start_notify(self, uuid: str, callback: Any) -> None:
        if FakeClient.notify_error:
            message = "no notify"
            raise BleakError(message)
        assert uuid == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback

    async def write_gatt_char(self, uuid: str, data: bytes, response: Any = None) -> None:
        if FakeClient.write_error:
            message = "write failed"
            raise BleakError(message)
        self.writes.append((uuid, bytes(data), response))
        if self.callback:
            self.callback(None, bytearray(b"\x01\x02"))


@pytest.fixture(autouse=False)
def fast_ble(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeClient.instances = []
    FakeClient.notify_error = False
    FakeClient.write_error = False
    monkeypatch.setattr(ble, "_INTER_CHUNK_DELAY_SECONDS", 0)


@pytest.mark.usefixtures("fast_ble")
def test_writer_writes_chunks_and_collects_notifications() -> None:
    progress: list[tuple[int, int]] = []
    writer = ble.BleakGattWriter(client_factory=FakeClient)
    notes = asyncio.run(
        writer.send(
            "AA",
            [b"1", b"2"],
            listen_seconds=0.01,
            on_progress=lambda d, t: progress.append((d, t)),
        ),
    )
    client = FakeClient.instances[0]
    assert [w[1] for w in client.writes] == [b"1", b"2"]
    assert progress == [(1, 2), (2, 2)]
    assert notes == (b"\x01\x02", b"\x01\x02")


@pytest.mark.usefixtures("fast_ble")
def test_writer_without_listening_and_without_notify_support() -> None:
    writer = ble.BleakGattWriter(client_factory=FakeClient)
    assert asyncio.run(writer.send("AA", [b"1"])) == ()
    FakeClient.notify_error = True
    assert asyncio.run(writer.send("AA", [b"1"], listen_seconds=0.01)) == ()


@pytest.mark.usefixtures("fast_ble")
def test_writer_wraps_errors() -> None:
    FakeClient.write_error = True
    writer = ble.BleakGattWriter(client_factory=FakeClient)
    with pytest.raises(BleError, match="write to AA failed"):
        asyncio.run(writer.send("AA", [b"1"]))
