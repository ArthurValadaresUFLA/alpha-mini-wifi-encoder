"""Encoder, BLE sender and programming-mode client for the UBTECH Alpha Mini.

The offline encoder reproduces, byte for byte, what the companion app would write to the
robot's GATT characteristic for Wi-Fi provisioning. The programming-mode client
(:class:`CodeMaoSession`) speaks the CodeMao skill over any frame transport; the BLE and LAN
transports live in :mod:`~.ble_transport` and :mod:`~.lan`, so importing this package never
opens a Bluetooth or network connection.

Public names re-exported here, with their types:

* ``WifiProvisioningEncoder`` (class): facade that encodes a provisioning request.
* ``WifiCredentials`` (Pydantic model): validated network description.
* ``EncodedProvisioning`` (frozen dataclass): JSON, frame, layers and GATT chunks.
* ``WifiCommand`` (enumeration): the ``co`` command values.
* ``CodeMaoCommand`` (enumeration): command ids of the programming skill.
* ``CodeMaoSession`` (class): request/response session for the programming skill.
* ``CommandResult`` and ``HandShakeResult`` (frozen dataclasses): robot answers.
* ``EncoderError`` (exception class): base class for package errors.
* ``PayloadTooLargeError`` (exception class): oversized structure error.

The ``__version__`` string holds the package version; ``__all__`` lists the public names above.
"""

from alpha_mini_wifi_encoder.codemao import CommandResult, HandShakeResult
from alpha_mini_wifi_encoder.constants import CodeMaoCommand, WifiCommand
from alpha_mini_wifi_encoder.encoder import EncodedProvisioning, WifiProvisioningEncoder
from alpha_mini_wifi_encoder.errors import EncoderError, PayloadTooLargeError
from alpha_mini_wifi_encoder.models import WifiCredentials
from alpha_mini_wifi_encoder.session import CodeMaoSession

__all__ = [
    "CodeMaoCommand",
    "CodeMaoSession",
    "CommandResult",
    "EncodedProvisioning",
    "EncoderError",
    "HandShakeResult",
    "PayloadTooLargeError",
    "WifiCommand",
    "WifiCredentials",
    "WifiProvisioningEncoder",
]
"""list[str]: Names re-exported as the package's public API."""
__version__ = "0.2.0"
"""str: Package version, also reported by the command-line ``--version`` option."""
