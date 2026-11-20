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
