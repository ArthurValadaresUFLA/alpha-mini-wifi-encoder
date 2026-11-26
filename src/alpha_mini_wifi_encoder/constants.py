"""Protocol constants recovered from the Alpha Mini companion app.

Every value here is documented in the analysis report; none of them is
inferred at runtime. GATT identifiers are listed for reference only: this
package never opens a BLE connection.

The module exports the frame markers and size limits used by the wire format,
the GATT chunking limits, the Wi-Fi credential bounds and the
:class:`WifiCommand` enumeration of JSON-level commands.
"""

from enum import IntEnum
from typing import Final

ENVELOPE_COMMAND_ID: Final[int] = 32
"""int: ``MessageHeader.commandId`` shared by every Wi-Fi/bind command."""

HEADER_VERSION_CODE: Final[int] = 1
"""int: ``MessageHeader.versionCode`` pinned by the app."""

BIND_REQUEST_VERSION_CODE: Final[int] = 168
"""int: ``BindOrSwitchWifiRequest.versionCode`` pinned by the app."""

FRAME_MAGIC: Final[bytes] = b"\xfb\xbf"
"""bytes: Two-byte start-of-frame marker of ``MiniBleProto``."""

FRAME_TYPE_SINGLE: Final[int] = 0x01
"""int: Frame type byte used for a single, non-fragmented frame."""

FRAME_TAIL: Final[bytes] = b"\x01\xed"
"""bytes: Trailer of one constant byte (``0x01``) and the ``0xED`` terminator."""

FRAME_OVERHEAD: Final[int] = len(FRAME_MAGIC) + 1 + 2 + len(FRAME_TAIL)
"""int: Bytes added around the content: magic, type, length and tail."""

MAX_FRAME_LENGTH_FIELD: Final[int] = 0xFFFF
"""int: Largest value of the unsigned 16-bit big-endian frame length field."""

GATT_CHUNK_PAYLOAD_SIZE: Final[int] = 16
"""int: Number of frame bytes carried by each GATT write."""

GATT_CHUNK_TRAILER_SIZE: Final[int] = 4
"""int: Random id (2 bytes) plus serial number (2 bytes), both big-endian."""

MAX_RANDOM_ID: Final[int] = 32766
"""int: Upper bound of ``Random().nextInt(32767)`` used by the app."""

MAX_SEND_SERIAL: Final[int] = 2**63 - 1
"""int: Largest value of the app-side ``AtomicLong`` used for ``sendSerial``."""

MAX_SSID_BYTES: Final[int] = 32
"""int: IEEE 802.11 SSID limit, in UTF-8 bytes."""

MAX_PASSWORD_BYTES: Final[int] = 64
"""int: Maximum password size in UTF-8 bytes (63 characters or a 64-digit PSK)."""

SERVICE_UUID: Final[str] = "10057e66-28bd-4c3e-a030-08b4982739bc"
"""str: BLE service UUID advertised by the robot for provisioning."""

WRITE_CHARACTERISTIC_UUID: Final[str] = "d2f9ff26-ad08-4bd3-8436-9b8f33bdea76"
"""str: Characteristic UUID accepting GATT write requests."""

NOTIFY_CHARACTERISTIC_UUID: Final[str] = "7e0dffa2-c44f-48ac-a39e-3fce46d9aabb"
"""str: Characteristic UUID used by the robot for notifications."""

HEARTBEAT_CHARACTERISTIC_UUID: Final[str] = "ebe066b9-a0c7-4ff4-9c05-6a58a9344364"
"""str: Characteristic UUID used for link heartbeat messages."""


class WifiCommand(IntEnum):
    """Enumerate the JSON-level commands (``co``) in the provisioning payload."""

    WIFI_INFO_TRANS = 2
    """int: Change the network of an already bound robot."""

    WIFI_INFO_TRANS_FOR_BINDING = 21
    """int: Send Wi-Fi information while binding the robot."""


class CodeMaoCommand(IntEnum):
    """Enumerate the ``MessageHeader.commandId`` values of the programming (CodeMao) skill."""

    REVERT_ORIGIN = 28
    """int: Return the robot to its origin pose (``RevertOriginRequest``)."""

    HANDSHAKE = 29
    """int: Start the programming skill (``HandShakeRequest``)."""

    CONTROL_RUNNING_PROGRAM = 33
    """int: Start or stop the running program (``ControlRunningProgramRequest``)."""


CODEMAO_HEADER_VERSION: Final[int] = 1
"""int: ``MessageHeader.versionCode`` used by the app for every CodeMao command."""

LAN_HANDSHAKE_REQUEST_COMMAND: Final[int] = 0
"""int: ``commandId`` of the LAN handshake request (inferred from the analysis report)."""

LAN_HANDSHAKE_RESPONSE_COMMAND: Final[int] = 1000
"""int: ``commandId`` of the expected LAN handshake response."""

LAN_KICKED_COMMAND: Final[int] = 18001
"""int: ``commandId`` announcing that another client took over the LAN channel."""

LAN_HANDSHAKE_TIMEOUT_SECONDS: Final[float] = 15.0
"""float: Seconds the app waits for the LAN handshake response."""

LAN_READ_TIMEOUT_SECONDS: Final[float] = 7.0
"""float: Seconds of read silence after which the app drops the LAN connection."""

LAN_WRITER_IDLE_SECONDS: Final[float] = 5.0
"""float: Seconds of write silence after which the app emits a heartbeat."""

LAN_HEARTBEAT_INBOUND_PING: Final[bytes] = b"\x00"
"""bytes: Heartbeat payload the robot sends; the client discards it (``HeartbeatHandler``)."""

LAN_HEARTBEAT_OUTBOUND_PONG: Final[bytes] = b"\x01"
"""bytes: Heartbeat payload the client writes whenever its writer has been idle."""

LAN_HEARTBEAT_PAYLOADS: Final[frozenset[bytes]] = frozenset(
    {LAN_HEARTBEAT_INBOUND_PING, LAN_HEARTBEAT_OUTBOUND_PONG},
)
"""frozenset[bytes]: Frame payloads treated as heartbeats and never delivered to sessions."""

LAN_MDNS_META_QUERY: Final[str] = "_services._dns-sd._udp.local."
"""str: DNS-SD meta-query; its answers are service *types*, not robots (``LanScanConfig``)."""

LAN_MDNS_SERVICE_TYPES: Final[tuple[str, ...]] = (
    LAN_MDNS_META_QUERY,
    "_DEDU_mini_robot_control._tcp.local.",
    "_Mini_mini_channel_server._tcp.local.",
    "_Dedu_mini_channel_server._tcp.local.",
)
"""tuple[str, ...]: Canonical mDNS types browsed by default: the app's constants and the
official SDK's (``RobotType.MINI`` and the DEDU default). The EDU/KOR variants of the SDK are not
listed because their exact names were not recovered."""

APP_VERSION_NAME: Final[str] = "1.3.2"
"""str: ``clientVersionName`` of the companion app this package mimics."""

APP_VERSION_CODE: Final[int] = 1030
"""int: ``clientVersionCode`` of the analysed build."""

DEVICE_TYPE_ANDROID_MOBILE: Final[int] = 1
"""int: ``deviceType`` value ``ANDROID_MOBILE`` of the LAN handshake."""

LAN_CHECKSUM_OFFSET: Final[int] = 2
"""int: Value added to the frame length field to obtain the LAN checksum byte."""
