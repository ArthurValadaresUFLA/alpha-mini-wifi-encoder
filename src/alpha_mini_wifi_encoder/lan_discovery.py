"""mDNS discovery of robots on the local network (via ``zeroconf``).

:class:`LanRobot` describes a discovered robot, :class:`LanScanner` is the strategy interface
and :class:`ZeroconfLanScanner` browses the service type found in the app. The type string is
taken literally from the analysis report and normalised with the leading ``_`` that mDNS
requires; the app's runtime browsing (it also holds ``_services._dns-sd._udp.local.``) was not
confirmed on hardware.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from alpha_mini_wifi_encoder.constants import LAN_MDNS_SERVICE_TYPE

if TYPE_CHECKING:
    from collections.abc import Callable

_RESOLVE_TIMEOUT_MS = 3000
"""int: Milliseconds allowed to resolve each discovered service."""


@dataclass(frozen=True, slots=True)
class LanRobot:
    """A robot found by mDNS.

    Attributes:
        name (str): mDNS instance name, which the app also uses as the robot identity.
        host (str): IPv4 address to connect to.
        port (int): TCP port of the control service.
    """

    name: str
    host: str
    port: int

    @property
    def label(self) -> str:
        """Build a menu label from the name and endpoint.

        Returns:
            str: ``"<name>  <host>:<port>"``.
        """
        return f"{self.name}  {self.host}:{self.port}"


class LanScanner(Protocol):
    """Strategy that discovers robots on the local network."""

    async def scan(self, timeout: float) -> list[LanRobot]:  # noqa: ASYNC109 - plain duration
        """Browse for ``timeout`` seconds and return the robots found.

        Args:
            timeout (float): Seconds to keep browsing.

        Returns:
            list[LanRobot]: Resolved robots, ordered by name.
        """
        ...


def normalise_service_type(service_type: str) -> str:
    """Ensure ``service_type`` carries the leading underscore mDNS requires.

    Args:
        service_type (str): Service type, with or without the leading ``_``.

    Returns:
        str: The type starting with ``_``.
    """
    return service_type if service_type.startswith("_") else f"_{service_type}"


class ZeroconfLanScanner:
    """Concrete :class:`LanScanner` backed by :mod:`zeroconf`."""

    def __init__(
        self,
        service_type: str = LAN_MDNS_SERVICE_TYPE,
        *,
        zeroconf_factory: Callable[[], Any] = AsyncZeroconf,
        browser_factory: Callable[..., Any] = AsyncServiceBrowser,
    ) -> None:
        """Create the scanner.

        Args:
            service_type (str): mDNS service type to browse.
            zeroconf_factory (Callable[[], Any]): Creates the async zeroconf instance.
            browser_factory (Callable[..., Any]): Creates the service browser.

        Returns:
            None: This method does not return a value.
        """
        self._service_type = normalise_service_type(service_type)
        self._zeroconf_factory = zeroconf_factory
        self._browser_factory = browser_factory

    async def scan(self, timeout: float) -> list[LanRobot]:  # noqa: ASYNC109 - plain duration
        """Browse the service type, then resolve every instance seen.

        Args:
            timeout (float): Seconds to keep browsing.

        Returns:
            list[LanRobot]: Robots with an IPv4 address, ordered by name.
        """
        names: set[str] = set()

        def on_change(
            *,
            name: str,
            state_change: ServiceStateChange,
            **_ignored: object,
        ) -> None:
            """Remember instances that appeared or changed.

            Args:
                name (str): mDNS instance name.
                state_change (ServiceStateChange): Kind of change.
                **_ignored (object): Remaining zeroconf handler arguments.

            Returns:
                None: This function does not return a value.
            """
            if state_change is not ServiceStateChange.Removed:
                names.add(name)

        zeroconf = self._zeroconf_factory()
        browser = self._browser_factory(
            zeroconf.zeroconf,
            [self._service_type],
            handlers=[on_change],
        )
        try:
            await asyncio.sleep(timeout)
            robots = [await self._resolve(zeroconf, name) for name in sorted(names)]
        finally:
            await browser.async_cancel()
            await zeroconf.async_close()
        return [robot for robot in robots if robot is not None]

    async def _resolve(self, zeroconf: Any, name: str) -> LanRobot | None:  # noqa: ANN401
        """Resolve one instance to an IPv4 endpoint.

        Args:
            zeroconf (Any): Async zeroconf instance.
            name (str): mDNS instance name.

        Returns:
            LanRobot | None: The robot, or ``None`` if it has no IPv4 address or port.
        """
        info = await zeroconf.async_get_service_info(
            self._service_type,
            name,
            timeout=_RESOLVE_TIMEOUT_MS,
        )
        if info is None or not info.port:
            return None
        addresses = info.parsed_addresses(IPVersion.V4Only)
        return LanRobot(name, addresses[0], info.port) if addresses else None
