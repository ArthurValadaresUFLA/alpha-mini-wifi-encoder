"""mDNS discovery of robots on the local network (via ``zeroconf``).

The scanner browses *every* service type the analysis found (the app's two constants and the
official SDK's) in a single run, expands the DNS-SD meta-query (``_services._dns-sd._udp.local.``)
into the service types it advertises, resolves each instance concurrently and keeps the TXT
records. :class:`DiscoveryReport` keeps per-type counters so that an empty result can be told
apart from "records seen but not resolved". An empty report is inconclusive: it never proves
that the robot has mDNS disabled, because multicast is link-local and may be filtered by the
access point.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from alpha_mini_wifi_encoder.constants import LAN_MDNS_META_QUERY, LAN_MDNS_SERVICE_TYPES

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_RESOLVE_TIMEOUT_MS = 3000
"""int: Milliseconds allowed to resolve each discovered service."""


@dataclass(frozen=True, slots=True)
class LanRobot:
    """A service instance found by mDNS.

    Attributes:
        name (str): mDNS instance name, which the app also uses as the robot identity.
        host (str): IPv4 address to connect to.
        port (int): TCP port advertised for the service.
        service_type (str): Service type the instance was found under.
        text (Mapping[str, str]): TXT record properties (semantics unknown).
    """

    name: str
    host: str
    port: int
    service_type: str = ""
    text: Mapping[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Build a menu label from the name and endpoint.

        Returns:
            str: ``"<name>  <host>:<port>"``.
        """
        return f"{self.name}  {self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """Result of one scan, with the counters needed to interpret an empty outcome.

    Attributes:
        types_browsed (tuple[str, ...]): Service types browsed, including expanded ones.
        seen_by_type (Mapping[str, int]): Instances announced per service type.
        advertised_types (tuple[str, ...]): Service types the meta-query reported.
        records (tuple[LanRobot, ...]): Instances resolved to an IPv4 endpoint.
        unresolved (tuple[str, ...]): Instances seen but not resolved to an IPv4 endpoint.
    """

    types_browsed: tuple[str, ...]
    seen_by_type: Mapping[str, int]
    advertised_types: tuple[str, ...]
    records: tuple[LanRobot, ...]
    unresolved: tuple[str, ...]

    @property
    def robots(self) -> tuple[LanRobot, ...]:
        """Return the records found under one of the robot service types.

        Returns:
            tuple[LanRobot, ...]: Records whose type matches, ignoring case, a default type.
        """
        known = {t.lower() for t in LAN_MDNS_SERVICE_TYPES}
        return tuple(r for r in self.records if r.service_type.lower() in known)

    @property
    def records_seen(self) -> int:
        """Count every instance announced, resolved or not.

        Returns:
            int: Total of ``seen_by_type``.
        """
        return sum(self.seen_by_type.values())

    @property
    def diagnostic(self) -> str:
        """Explain the outcome without over-concluding from an empty result.

        Returns:
            str: A one-line diagnostic.
        """
        if self.robots:
            return f"{len(self.robots)} robot service(s) resolved."
        if self.unresolved:
            return (
                "records seen but none resolved to an IPv4 endpoint; retry with a longer timeout."
            )
        if self.records_seen == 0:
            return (
                "no announcement in the queried types; do not conclude that mDNS is disabled: "
                "check multicast/AP isolation, that you are on the robot's network, and that "
                "the firmware announces."
            )
        return "services found, but none under a robot service type; inspect the TXT/other types."


class LanScanner(Protocol):
    """Strategy that discovers robots on the local network."""

    async def scan(self, timeout: float) -> DiscoveryReport:  # noqa: ASYNC109 - plain duration
        """Browse for ``timeout`` seconds and report what was found.

        Args:
            timeout (float): Seconds to keep browsing.

        Returns:
            DiscoveryReport: Counters, resolved records and unresolved names.
        """
        ...


def _decode_text(properties: Mapping[bytes, bytes | None]) -> dict[str, str]:
    """Decode TXT properties for display, replacing undecodable bytes.

    Args:
        properties (Mapping[bytes, bytes | None]): Raw TXT properties.

    Returns:
        dict[str, str]: Keys and values as text; missing values become empty strings.
    """
    return {
        key.decode("utf-8", "replace"): (value or b"").decode("utf-8", "replace")
        for key, value in properties.items()
    }


class ZeroconfLanScanner:
    """Concrete :class:`LanScanner` backed by :mod:`zeroconf`."""

    def __init__(
        self,
        service_types: Sequence[str] = LAN_MDNS_SERVICE_TYPES,
        *,
        interfaces: Sequence[str] | None = None,
        zeroconf_factory: Callable[..., Any] = AsyncZeroconf,
        browser_factory: Callable[..., Any] = AsyncServiceBrowser,
    ) -> None:
        """Create the scanner.

        Args:
            service_types (Sequence[str]): Canonical service types to browse (with the leading
                ``_``); the meta-query is expanded.
            interfaces (Sequence[str] | None): Local addresses to bind multicast to, or
                ``None`` for the zeroconf default.
            zeroconf_factory (Callable[..., Any]): Creates the async zeroconf instance.
            browser_factory (Callable[..., Any]): Creates the service browser.

        Returns:
            None: This method does not return a value.
        """
        self._service_types = tuple(service_types)
        self._interfaces = list(interfaces) if interfaces else None
        self._zeroconf_factory = zeroconf_factory
        self._browser_factory = browser_factory

    async def scan(self, timeout: float) -> DiscoveryReport:  # noqa: ASYNC109 - plain duration
        """Browse all types, expand the meta-query, then resolve every instance seen.

        Args:
            timeout (float): Seconds to keep browsing (split between the two phases).

        Returns:
            DiscoveryReport: Counters, IPv4-resolved records (sorted) and unresolved names.
        """
        kwargs: dict[str, Any] = {"ip_version": IPVersion.V4Only}
        if self._interfaces:
            kwargs["interfaces"] = self._interfaces
        zeroconf = self._zeroconf_factory(**kwargs)
        browsers: list[Any] = []
        seen: dict[tuple[str, str], None] = {}
        advertised: set[str] = set()

        def on_change(
            *,
            service_type: str,
            name: str,
            state_change: ServiceStateChange,
            **_ignored: object,
        ) -> None:
            """Record announcements; the meta-query's answers are types, not instances.

            Args:
                service_type (str): Type the announcement was received for.
                name (str): Announced name.
                state_change (ServiceStateChange): Kind of change.
                **_ignored (object): Remaining zeroconf handler arguments.

            Returns:
                None: This function does not return a value.
            """
            if state_change is ServiceStateChange.Removed:
                return
            if service_type.lower() == LAN_MDNS_META_QUERY:
                advertised.add(name)
            else:
                seen[(service_type, name)] = None

        def browse(types: Sequence[str]) -> None:
            """Start one browser for ``types``.

            Args:
                types (Sequence[str]): Service types to browse.

            Returns:
                None: This function does not return a value.
            """
            browsers.append(
                self._browser_factory(zeroconf.zeroconf, list(types), handlers=[on_change]),
            )

        first_phase = timeout / 2
        browsed = list(self._service_types)
        try:
            browse(browsed)
            await asyncio.sleep(first_phase)
            extra = sorted(t for t in advertised if t.lower() not in {b.lower() for b in browsed})
            if extra:
                browse(extra)
                browsed.extend(extra)
            await asyncio.sleep(timeout - first_phase)
            keys = sorted(seen)
            resolved = await asyncio.gather(*(self._resolve(zeroconf, t, n) for t, n in keys))
        finally:
            for browser in browsers:
                await browser.async_cancel()
            await zeroconf.async_close()
        records = tuple(sorted((r for r in resolved if r is not None), key=lambda r: r.name))
        unresolved = tuple(
            name for (_, name), robot in zip(keys, resolved, strict=True) if robot is None
        )
        return DiscoveryReport(
            types_browsed=tuple(browsed),
            seen_by_type=dict(Counter(service_type for service_type, _ in keys)),
            advertised_types=tuple(sorted(advertised)),
            records=records,
            unresolved=unresolved,
        )

    @staticmethod
    async def _resolve(zeroconf: Any, service_type: str, name: str) -> LanRobot | None:  # noqa: ANN401
        """Resolve one instance to an IPv4 endpoint with its TXT record.

        Args:
            zeroconf (Any): Async zeroconf instance.
            service_type (str): Type the instance was announced under.
            name (str): mDNS instance name.

        Returns:
            LanRobot | None: The record, or ``None`` if it has no IPv4 address or port.
        """
        info = await zeroconf.async_get_service_info(
            service_type, name, timeout=_RESOLVE_TIMEOUT_MS
        )
        if info is None or not info.port:
            return None
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not addresses:
            return None
        return LanRobot(
            name,
            addresses[0],
            info.port,
            service_type,
            _decode_text(info.properties or {}),
        )
