"""Starlette integration.

Provides ``setup_doppy`` which registers a ``BaseHTTPMiddleware`` that creates
a per-request scope and exposes it via ``request.state``.

Examples:
    >>> from starlette.applications import Starlette
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.starlette import setup_doppy
    >>> app = Starlette()
    >>> services = Container()
    >>> setup_doppy(app, services)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from doppy_di.container import Container


def setup_doppy(
    app: Any,
    container: Container,
    scope: str = "request",
) -> None:
    """Register request-scope middleware on a Starlette app.

    Args:
        app: Starlette application.
        container: Container used for resolution.
        scope: Scope name created per request.
    """

    from starlette.middleware.base import BaseHTTPMiddleware

    class _DoppyScopeMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self,
            request: Any,
            call_next: Callable[[Any], Awaitable[Any]],
        ) -> Any:
            async with container.ascope(scope) as s:
                s.set_context("request", request)
                s.set_context("scope", s)
                request.state.doppy_scope = s
                return await call_next(request)

    app.add_middleware(_DoppyScopeMiddleware)
