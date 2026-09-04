"""Aiohttp integration.

Provides ``setup_doppy`` which registers web middleware that creates a
per-request scope and exposes it via ``request``.

Examples:
    >>> from aiohttp import web
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.aiohttp import setup_doppy
    >>> app = web.Application()
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
) -> Any:
    """Register request-scope middleware on an aiohttp app.

    Args:
        app: aiohttp ``web.Application``.
        container: Container used for resolution.
        scope: Scope name created per request.

    Returns:
        The same app (middleware registered in place).
    """
    from aiohttp import web

    @web.middleware
    async def _doppy_scope(
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        async with container.ascope(scope) as s:
            s.set_context("request", request)
            s.set_context("scope", s)
            request["doppy_scope"] = s
            return await handler(request)

    app.middlewares.append(_doppy_scope)
    return app
