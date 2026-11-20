"""Offline encoder for the UBTECH Alpha Mini BLE Wi-Fi provisioning payload.

The package reproduces, byte for byte, what the companion app would write to the
robot's GATT characteristic. It never performs any BLE I/O.

Public names re-exported here, with their types:

* ``WifiProvisioningEncoder`` (class): facade that encodes a provisioning request.
* ``WifiCredentials`` (Pydantic model): validated network description.
* ``EncodedProvisioning`` (frozen dataclass): JSON, frame, layers and GATT chunks.
* ``WifiCommand`` (enumeration): the ``co`` command values.
* ``EncoderError`` (exception class): base class for package errors.
* ``PayloadTooLargeError`` (exception class): oversized structure error.

The ``__version__`` string holds the package version; ``__all__`` lists the six
public classes above.
"""

from alpha_mini_wifi_encoder.constants import WifiCommand
from alpha_mini_wifi_encoder.encoder import EncodedProvisioning, WifiProvisioningEncoder
from alpha_mini_wifi_encoder.errors import EncoderError, PayloadTooLargeError
from alpha_mini_wifi_encoder.models import WifiCredentials

__all__ = [
    "EncodedProvisioning",
    "EncoderError",
    "PayloadTooLargeError",
    "WifiCommand",
    "WifiCredentials",
    "WifiProvisioningEncoder",
]
"""list[str]: Names re-exported as the package's public API."""
__version__ = "0.1.0"
"""str: Package version, also reported by the command-line ``--version`` option."""
