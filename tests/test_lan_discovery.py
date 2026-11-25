import asyncio
from collections.abc import Callable
from typing import Any

from zeroconf import ServiceStateChange

from alpha_mini_wifi_encoder.lan_discovery import (
    LanRobot,
    ZeroconfLanScanner,
    normalise_service_type,
)


class FakeInfo:
    def __init__(self, port: int, addresses: list[str]) -> None:
        self.port = port
        self._addresses = addresses

    def parsed_addresses(self, version: object = None) -> list[str]:
        return self._addresses


class FakeZeroconf:
    def __init__(self, infos: dict[str, FakeInfo | None]) -> None:
        self.zeroconf = object()
        self.infos = infos
        self.closed = False
        self.queried: list[tuple[str, str]] = []

    async def async_get_service_info(self, type_: str, name: str, timeout: int) -> FakeInfo | None:
        self.queried.append((type_, name))
        return self.infos[name]

    async def async_close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(
        self, events: list[tuple[str, ServiceStateChange]], handlers: list[Callable[..., None]]
    ) -> None:
        self.cancelled = False
        for name, change in events:
            for handler in handlers:
                handler(zeroconf=None, service_type="t", name=name, state_change=change)

    async def async_cancel(self) -> None:
        self.cancelled = True


def test_normalise_service_type() -> None:
    assert normalise_service_type("DEDU_x._tcp.local.") == "_DEDU_x._tcp.local."
    assert normalise_service_type("_ok._tcp.local.") == "_ok._tcp.local."


def test_label() -> None:
    assert LanRobot("mini", "10.0.0.2", 8000).label == "mini  10.0.0.2:8000"


def test_scan_resolves_sorted_and_skips_unusable_entries() -> None:
    zeroconf = FakeZeroconf(
        {
            "b._t": FakeInfo(8001, ["10.0.0.3"]),
            "a._t": FakeInfo(8000, ["10.0.0.2", "10.0.0.9"]),
            "noport._t": FakeInfo(0, ["10.0.0.4"]),
            "noaddr._t": FakeInfo(1, []),
            "none._t": None,
        }
    )
    browsers: list[FakeBrowser] = []

    def browser_factory(
        zc: Any, types: list[str], handlers: list[Callable[..., None]]
    ) -> FakeBrowser:
        assert types == ["_DEDU_mini_robot_control._tcp.local."]
        events = [(name, ServiceStateChange.Added) for name in zeroconf.infos]
        events += [("gone._t", ServiceStateChange.Removed), ("a._t", ServiceStateChange.Updated)]
        browsers.append(FakeBrowser(events, handlers))
        return browsers[-1]

    scanner = ZeroconfLanScanner(zeroconf_factory=lambda: zeroconf, browser_factory=browser_factory)
    robots = asyncio.run(scanner.scan(0))
    assert robots == [LanRobot("a._t", "10.0.0.2", 8000), LanRobot("b._t", "10.0.0.3", 8001)]
    assert browsers[0].cancelled
    assert zeroconf.closed
    assert "gone._t" not in {name for _, name in zeroconf.queried}
