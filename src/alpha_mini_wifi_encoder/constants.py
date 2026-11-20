"""Protocol constants recovered from the Alpha Mini companion app.

Every value here is documented in the analysis report; none of them is
inferred at runtime. GATT identifiers are listed for reference only: this
package never opens a BLE connection.
"""

from enum import IntEnum
from typing import Final

ENVELOPE_COMMAND_ID: Final[int] = 32
"""``MessageHeader.commandId`` shared by every Wi-Fi/bind command."""

HEADER_VERSION_CODE: Final[int] = 1
"""``MessageHeader.versionCode`` pinned by the app."""

BIND_REQUEST_VERSION_CODE: Final[int] = 168
"""``BindOrSwitchWifiRequest.versionCode`` pinned by the app."""

FRAME_MAGIC: Final[bytes] = b"\xfb\xbf"
"""Two-byte start-of-frame marker of ``MiniBleProto``."""

FRAME_TYPE_SINGLE: Final[int] = 0x01
"""Frame type byte used for a single, non-fragmented frame."""

FRAME_TAIL: Final[bytes] = b"\x01\xed"
"""Trailer: one constant byte (``0x01``) followed by the ``0xED`` terminator."""

FRAME_OVERHEAD: Final[int] = len(FRAME_MAGIC) + 1 + 2 + len(FRAME_TAIL)
"""Bytes added around the content: magic, type, length and tail."""

MAX_FRAME_LENGTH_FIELD: Final[int] = 0xFFFF
"""The frame length field is an unsigned 16-bit big-endian integer."""

GATT_CHUNK_PAYLOAD_SIZE: Final[int] = 16
"""Frame bytes carried by each GATT write."""

GATT_CHUNK_TRAILER_SIZE: Final[int] = 4
"""Random id (2 bytes) plus serial number (2 bytes), both big-endian."""

MAX_RANDOM_ID: Final[int] = 32766
"""Upper bound of ``Random().nextInt(32767)`` used by the app."""

MAX_SEND_SERIAL: Final[int] = 2**63 - 1
"""``sendSerial`` is an ``AtomicLong`` on the app side."""

MAX_SSID_BYTES: Final[int] = 32
"""IEEE 802.11 SSID limit, in UTF-8 bytes."""

MAX_PASSWORD_BYTES: Final[int] = 64
"""WPA passphrases hold up to 63 characters; a raw PSK is 64 hex digits."""

SERVICE_UUID: Final[str] = "10057e66-28bd-4c3e-a030-08b4982739bc"
WRITE_CHARACTERISTIC_UUID: Final[str] = "d2f9ff26-ad08-4bd3-8436-9b8f33bdea76"
NOTIFY_CHARACTERISTIC_UUID: Final[str] = "7e0dffa2-c44f-48ac-a39e-3fce46d9aabb"
HEARTBEAT_CHARACTERISTIC_UUID: Final[str] = "ebe066b9-a0c7-4ff4-9c05-6a58a9344364"


class WifiCommand(IntEnum):
    """JSON-level command (``co``) carried inside the provisioning payload."""

    WIFI_INFO_TRANS = 2
    """Send Wi-Fi information to change the network of an already bound robot."""

    WIFI_INFO_TRANS_FOR_BINDING = 21
    """Send Wi-Fi information while binding the robot."""
