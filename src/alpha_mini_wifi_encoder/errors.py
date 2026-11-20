"""Exception hierarchy of the package."""


class EncoderError(Exception):
    """Base class for every error raised deliberately by this package."""


class PayloadTooLargeError(EncoderError):
    """Raised when an encoded structure no longer fits its wire-format field."""
