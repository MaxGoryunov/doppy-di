"""Typer integration.

Provides ``setup_doppy`` which wraps registered commands with ``@inject``
for automatic dependency injection.

Example:
    >>> from typer import Typer
    >>> from doppy_di.container import ContainerBuilder
    >>> from doppy_di.ext.typer import setup_doppy
    >>> app = Typer()
    >>> container = ContainerBuilder().build()
    >>> setup_doppy(app, container)
"""

from __future__ import annotations

from typing import Any

from doppy_di.container import Container
from doppy_di.inject import inject


def setup_doppy(
    app: Any,
    container: Container,
) -> None:
    """Wrap all registered Typer commands with ``@inject``.

    Args:
        app: Typer application.
        container: Container used for resolution.
    """
    for command in app.registered_commands:
        if command.callback is not None:
            command.callback = inject(container=container)(command.callback)
