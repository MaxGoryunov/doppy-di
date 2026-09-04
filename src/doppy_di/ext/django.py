"""Django integration.

Provides ``setup_doppy`` which returns a Django middleware class that creates
a per-request scope and exposes it via ``request.doppy_scope``.

Examples:
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.django import setup_doppy
    >>> services = Container()
    >>> DoppyMiddleware = setup_doppy(services)
"""

from __future__ import annotations

from typing import Any, Callable

from doppy_di.container import Container


def setup_doppy(
    container: Container,
    scope: str = "request",
) -> Any:
    """Return a Django middleware class bound to ``container``.

    Django instantiates each ``MIDDLEWARE`` entry with ``get_response``.

    Args:
        container: Container used for resolution.
        scope: Scope name created per request.

    Returns:
        A Django middleware class.
    """
    bound_scope = scope

    class DoppyMiddleware:
        """Django middleware opening a per-request scope."""

        def __init__(self, get_response: Callable[[Any], Any]) -> None:
            self.get_response = get_response

        def __call__(self, request: Any) -> Any:
            with container.scope(bound_scope) as s:
                s.set_context("request", request)
                request.doppy_scope = s
                return self.get_response(request)

    return DoppyMiddleware
