"""Helpers shared by the click front ends.

The module exports :func:`format_validation_error`, which renders a Pydantic error on one
line, and :func:`handle_errors`, a decorator translating library exceptions to click exceptions.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

import click
from pydantic import ValidationError

from alpha_mini_wifi_encoder.errors import EncoderError, UserAbortedError

if TYPE_CHECKING:
    from collections.abc import Callable


def format_validation_error(error: ValidationError) -> str:
    """Format a Pydantic validation error as one readable line.

    Args:
        error (ValidationError): Validation failure raised by a Pydantic model.

    Returns:
        str: Semicolon-separated ``field: message`` pairs from ``error.errors()``.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


def handle_errors[**P](function: Callable[P, None]) -> Callable[P, None]:
    """Translate library exceptions to click exceptions.

    Args:
        function (Callable[P, None]): Click command callback to wrap.

    Returns:
        Callable[P, None]: A wrapper with the same signature that reports library
            errors as click exceptions.
    """

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        """Call the decorated callback and translate its library exceptions.

        Args:
            args (P.args): Positional arguments forwarded to the callback.
            kwargs (P.kwargs): Keyword arguments forwarded to the callback.

        Returns:
            None: This function does not return a value.

        Raises:
            click.UsageError: If the callback raises a ``ValidationError``.
            click.Abort: If the callback raises a ``UserAbortedError``.
            click.ClickException: If the callback raises another ``EncoderError``.
        """
        try:
            function(*args, **kwargs)
        except ValidationError as error:
            raise click.UsageError(format_validation_error(error)) from error
        except UserAbortedError as error:
            raise click.Abort from error
        except EncoderError as error:
            raise click.ClickException(str(error)) from error

    return wrapper
