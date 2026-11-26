import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from zeroconf import IPVersion, ServiceStateChange

from alpha_mini_wifi_encoder.constants import LAN_MDNS_META_QUERY, LAN_MDNS_SERVICE_TYPES
from alpha_mini_wifi_encoder.lan_discovery import DiscoveryReport, LanRobot, ZeroconfLanScanner

KNOWN = "_DEDU_mini_robot_control._tcp.local."
SDK = "_Mini_mini_channel_server._tcp.local."
OTHER = "_other._tcp.local."


class FakeInfo:
    def __init__(
        self,
        port: int,
        addresses: list[str],
        properties: dict[bytes, bytes | None] | None = None,
    ) -> None:
        self.port = port
        self._addresses = addresses
        self.properties = properties or {}

    def parsed_addresses(self, version: object = None) -> list[str]:
        assert version is IPVersion.V4Only
        return self._addresses


class FakeZeroconf:
    def __init__(self, infos: dict[tuple[str, str], FakeInfo | None], **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.zeroconf = object()
        self.infos = infos
        self.closed = False

    async def async_get_service_info(self, type_: str, name: str, timeout: int) -> FakeInfo | None:
        return self.infos[(type_, name)]

    async def async_close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.cancelled = False

    async def async_cancel(self) -> None:
        self.cancelled = True


class Network:
    """Wires a fake zeroconf and fake browsers announcing ``events`` per service type."""

    def __init__(
        self,
        events: dict[str, list[tuple[str, ServiceStateChange]]],
        infos: dict[tuple[str, str], FakeInfo | None],
    ) -> None:
        self.events = events
        self.zeroconf = FakeZeroconf(infos)
        self.browsed: list[list[str]] = []
        self.browsers: list[FakeBrowser] = []
        self.zeroconf_kwargs: dict[str, Any] = {}

    def make_zeroconf(self, **kwargs: Any) -> FakeZeroconf:
        self.zeroconf_kwargs = kwargs
        return self.zeroconf

    def make_browser(
        self, _zc: object, types: list[str], handlers: list[Callable[..., None]]
    ) -> FakeBrowser:
        self.browsed.append(list(types))
        for service_type in types:
            for name, change in self.events.get(service_type, []):
                for handler in handlers:
                    handler(
                        zeroconf=None, service_type=service_type, name=name, state_change=change
                    )
        browser = FakeBrowser()
        self.browsers.append(browser)
        return browser

    def scanner(self, **kwargs: Any) -> ZeroconfLanScanner:
        return ZeroconfLanScanner(
            zeroconf_factory=self.make_zeroconf, browser_factory=self.make_browser, **kwargs
        )


ADDED = ServiceStateChange.Added


def test_default_types_cover_app_and_sdk_and_are_normalised() -> None:
    assert LAN_MDNS_META_QUERY in LAN_MDNS_SERVICE_TYPES
    assert KNOWN in LAN_MDNS_SERVICE_TYPES
    assert SDK in LAN_MDNS_SERVICE_TYPES
    assert all(t.startswith("_") and t.endswith(".local.") for t in LAN_MDNS_SERVICE_TYPES)


def test_label() -> None:
    assert LanRobot("mini", "10.0.0.2", 7000).label == "mini  10.0.0.2:7000"


def test_all_types_are_browsed_in_one_run_with_v4_only() -> None:
    network = Network({}, {})
    report = asyncio.run(network.scanner().scan(0))
    assert network.browsed == [list(LAN_MDNS_SERVICE_TYPES)]
    assert network.zeroconf_kwargs == {"ip_version": IPVersion.V4Only}
    assert report.types_browsed == LAN_MDNS_SERVICE_TYPES
    assert report.records_seen == 0
    assert network.zeroconf.closed
    assert all(b.cancelled for b in network.browsers)


def test_interfaces_are_passed_to_zeroconf() -> None:
    network = Network({}, {})
    asyncio.run(network.scanner(interfaces=["192.168.0.10"]).scan(0))
    assert network.zeroconf_kwargs["interfaces"] == ["192.168.0.10"]


def test_meta_query_is_expanded_not_resolved_as_a_robot() -> None:
    network = Network(
        {
            LAN_MDNS_META_QUERY: [
                (OTHER, ADDED),
                (KNOWN.lower(), ADDED),  # same type, different case: not browsed twice
                (SDK, ServiceStateChange.Removed),  # removals are ignored
            ],
            OTHER: [("printer._other._tcp.local.", ADDED)],
            KNOWN: [("mini-1._DEDU_mini_robot_control._tcp.local.", ADDED)],
        },
        {
            (OTHER, "printer._other._tcp.local."): FakeInfo(631, ["10.0.0.9"]),
            (KNOWN, "mini-1._DEDU_mini_robot_control._tcp.local."): FakeInfo(
                7000, ["10.0.0.2", "10.0.0.3"]
            ),
        },
    )
    report = asyncio.run(network.scanner().scan(0))
    assert network.browsed[1] == [OTHER]
    assert report.advertised_types == tuple(sorted([OTHER, KNOWN.lower()]))  # SDK was removed
    assert report.types_browsed[-1] == OTHER
    assert report.seen_by_type == {OTHER: 1, KNOWN: 1}
    assert [r.name for r in report.records] == [
        "mini-1._DEDU_mini_robot_control._tcp.local.",
        "printer._other._tcp.local.",
    ]
    assert [r.name for r in report.robots] == ["mini-1._DEDU_mini_robot_control._tcp.local."]
    assert report.records[0].host == "10.0.0.2"
    assert len(network.browsers) == 2


def test_txt_records_are_kept_and_decoded() -> None:
    name = f"r._{SDK}"
    network = Network(
        {SDK: [(name, ADDED)]},
        {
            (SDK, name): FakeInfo(
                7000, ["10.0.0.2"], {b"sn": b"ABC", b"flag": None, b"bad": b"\xff"}
            )
        },
    )
    (record,) = asyncio.run(network.scanner().scan(0)).records
    assert record.text == {"sn": "ABC", "flag": "", "bad": "\ufffd"}


def test_unresolved_instances_are_reported() -> None:
    network = Network(
        {KNOWN: [("a", ADDED), ("b", ADDED), ("c", ADDED), ("d", ADDED)]},
        {
            (KNOWN, "a"): None,
            (KNOWN, "b"): FakeInfo(0, ["10.0.0.4"]),
            (KNOWN, "c"): FakeInfo(7000, []),
            (KNOWN, "d"): FakeInfo(7000, ["10.0.0.5"]),
        },
    )
    report = asyncio.run(network.scanner().scan(0))
    assert report.unresolved == ("a", "b", "c")
    assert [r.name for r in report.records] == ["d"]
    assert report.records_seen == 4


class TestDiagnostic:
    def report(self, **kwargs: Any) -> DiscoveryReport:
        values: dict[str, Any] = {
            "types_browsed": (),
            "seen_by_type": {},
            "advertised_types": (),
            "records": (),
            "unresolved": (),
        }
        return DiscoveryReport(**{**values, **kwargs})

    def test_robots_found(self) -> None:
        robot = LanRobot("n", "h", 1, KNOWN.upper())
        report = self.report(records=(robot,), seen_by_type={KNOWN: 1})
        assert "1 robot service(s)" in report.diagnostic

    def test_unresolved(self) -> None:
        report = self.report(seen_by_type={KNOWN: 1}, unresolved=("n",))
        assert "none resolved" in report.diagnostic

    def test_nothing_seen_is_inconclusive(self) -> None:
        text = self.report().diagnostic
        assert "do not conclude" in text
        assert "AP isolation" in text

    @pytest.mark.parametrize("seen", [{OTHER: 2}])
    def test_other_types_only(self, seen: dict[str, int]) -> None:
        report = self.report(seen_by_type=seen, records=(LanRobot("p", "h", 1, OTHER),))
        assert "none under a robot service type" in report.diagnostic
