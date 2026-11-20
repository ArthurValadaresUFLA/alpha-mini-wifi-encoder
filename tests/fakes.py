"""Test doubles for the interactive and BLE collaborators."""

from collections.abc import Callable, Sequence

from alpha_mini_wifi_encoder.ble import BleDevice
from alpha_mini_wifi_encoder.errors import BleError
from alpha_mini_wifi_encoder.network import DetectedNetwork, NetworkDetectionError
from alpha_mini_wifi_encoder.saved_networks import SavedNetwork


class FakePrompter:
    """Scripted prompter: select answers are label substrings, others are literal values."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.menus: list[list[str]] = []
        self.password_messages: list[str] = []

    def select[T](self, message: str, choices: Sequence[tuple[str, T]]) -> T:
        self.menus.append([label for label, _ in choices])
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        for label, value in choices:
            if str(answer) in label:
                return value
        msg = f"{answer!r} not in {[label for label, _ in choices]}"
        raise AssertionError(msg)

    def text(self, message: str) -> str:
        return str(self.answers.pop(0))

    def password(self, message: str) -> str:
        self.password_messages.append(message)
        return str(self.answers.pop(0))

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return bool(self.answers.pop(0))


class FakeDetector:
    def __init__(self, network: DetectedNetwork | None) -> None:
        self._network = network

    def detect(self) -> DetectedNetwork:
        if self._network is None:
            msg = "not connected"
            raise NetworkDetectionError(msg)
        return self._network


class FakeSaved:
    def __init__(self, networks: list[SavedNetwork] | Exception) -> None:
        self._networks = networks

    def list_networks(self) -> list[SavedNetwork]:
        if isinstance(self._networks, Exception):
            raise self._networks
        return self._networks


class FakeScanner:
    def __init__(self, *scans: list[BleDevice]) -> None:
        self.scans = list(scans)
        self.calls = 0

    async def scan(self, timeout: float) -> list[BleDevice]:
        self.calls += 1
        return self.scans.pop(0) if len(self.scans) > 1 else self.scans[0]


class FakeWriter:
    def __init__(
        self, notifications: tuple[bytes, ...] = (), error: BleError | None = None
    ) -> None:
        self.notifications = notifications
        self.error = error
        self.address: str | None = None
        self.chunks: tuple[bytes, ...] = ()
        self.listen: float | None = None

    async def send(
        self,
        address: str,
        chunks: Sequence[bytes],
        *,
        listen_seconds: float = 0.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[bytes, ...]:
        if self.error is not None:
            raise self.error
        self.address, self.chunks, self.listen = address, tuple(chunks), listen_seconds
        for index in range(1, len(chunks) + 1):
            if on_progress:
                on_progress(index, len(chunks))
        return self.notifications
