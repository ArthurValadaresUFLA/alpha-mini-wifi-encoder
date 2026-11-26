"""Passive TCP reachability probe for a handful of ports.

:func:`probe_ports` only opens a TCP connection and closes it at once: no byte of any protocol is
written, no banner is read and no handshake is attempted. The result records the tool and the
exact port list so that a later "closed" can be interpreted, which an unrecorded scan cannot.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

type OpenConnection = Callable[
    [str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]

PROBE_TOOL = "asyncio TCP connect (no payload sent)"
"""str: Description of the method, recorded in every report."""

DEFAULT_DIAG_PORTS: tuple[int, ...] = (7000, 8801, 5555)
"""tuple[int, ...]: Ports of interest: 7000 (observed), 8801 (SDK) and 5555 (classic ADB)."""


@dataclass(frozen=True, slots=True)
class PortResult:
    """Reachability of one TCP port.

    Attributes:
        port (int): Port probed.
        is_open (bool): Whether the TCP connection was accepted.
        detail (str): ``"open"``, ``"refused"``, ``"timeout"`` or the OS error.
    """

    port: int
    is_open: bool
    detail: str


async def _probe_one(
    host: str,
    port: int,
    timeout: float,  # noqa: ASYNC109 - plain duration
    opener: OpenConnection,
) -> PortResult:
    """Try one connection and close it immediately.

    Args:
        host (str): Target address.
        port (int): Target port.
        timeout (float): Seconds allowed for the connection.
        opener (OpenConnection): Opens the connection.

    Returns:
        PortResult: The outcome.
    """
    try:
        async with asyncio.timeout(timeout):
            _, writer = await opener(host, port)
    except TimeoutError:
        return PortResult(port, is_open=False, detail="timeout")
    except ConnectionRefusedError:
        return PortResult(port, is_open=False, detail="refused")
    except OSError as error:
        return PortResult(port, is_open=False, detail=str(error))
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return PortResult(port, is_open=True, detail="open")


async def probe_ports(
    host: str,
    ports: Sequence[int] = DEFAULT_DIAG_PORTS,
    *,
    timeout: float = 2.0,  # noqa: ASYNC109 - plain duration
    opener: OpenConnection = asyncio.open_connection,
) -> list[PortResult]:
    """Check which of ``ports`` accept a TCP connection, sending nothing.

    Args:
        host (str): Target address.
        ports (Sequence[int]): Ports to probe.
        timeout (float): Seconds allowed per port.
        opener (OpenConnection): Opens a connection (injectable for tests).

    Returns:
        list[PortResult]: One result per port, in the order given.
    """
    return list(await asyncio.gather(*(_probe_one(host, p, timeout, opener) for p in ports)))
