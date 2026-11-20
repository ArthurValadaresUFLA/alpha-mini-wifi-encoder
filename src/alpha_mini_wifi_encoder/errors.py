"""Exception hierarchy of the package."""


class EncoderError(Exception):
    """Base class for every error raised deliberately by this package."""


class PayloadTooLargeError(EncoderError):
    """Raised when an encoded structure no longer fits its wire-format field."""


class FrameError(EncoderError):
    """Raised when bytes do not form a valid ``MiniBleProto`` frame."""


class BleError(EncoderError):
    """Raised when a Bluetooth scan, connection or write fails."""


class UserAbortedError(EncoderError):
    """Raised when the user cancels an interactive prompt."""
