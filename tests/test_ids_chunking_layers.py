import pytest

from alpha_mini_wifi_encoder.chunking import TrailerChunker
from alpha_mini_wifi_encoder.errors import PayloadTooLargeError
from alpha_mini_wifi_encoder.ids import FixedRandomId, SystemRandomId
from alpha_mini_wifi_encoder.layers import MessageLayer


@pytest.mark.parametrize("value", [-1, 32767])
def test_fixed_random_id_range(value: int) -> None:
    with pytest.raises(ValueError, match="random id"):
        FixedRandomId(value)


def test_system_random_id_in_range() -> None:
    provider = SystemRandomId()
    assert all(0 <= provider.next_id() <= 32766 for _ in range(200))


def test_chunker_same_random_id_and_increasing_serial() -> None:
    chunks = TrailerChunker(FixedRandomId(0x0102)).split(bytes(range(20)))
    assert [c[-4:] for c in chunks] == [bytes.fromhex("01020001"), bytes.fromhex("01020002")]
    assert len(chunks[0]) == 20
    assert len(chunks[1]) == 8


def test_chunker_rejects_bad_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        TrailerChunker(FixedRandomId(), chunk_size=0)


def test_chunker_rejects_serial_overflow() -> None:
    chunker = TrailerChunker(FixedRandomId(), chunk_size=1)
    with pytest.raises(PayloadTooLargeError):
        chunker.split(b"x" * 0x10000)


@pytest.mark.parametrize("serial", [-1, 2**63])
def test_message_layer_rejects_bad_serial(serial: int) -> None:
    with pytest.raises(ValueError, match="send_serial"):
        MessageLayer(send_serial=serial)
