"""Offline encoder for the UBTECH Alpha Mini BLE Wi-Fi provisioning payload.

The package reproduces, byte for byte, what the companion app would write to the
robot's GATT characteristic. It never performs any BLE I/O.
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
__version__ = "0.1.0"
