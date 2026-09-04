"""Sanic integration.

Provides ``setup_doppy`` which registers request/response middleware that
create a per-request scope, exposed via ``request.ctx.doppy_scope``.

Examples:
    >>> from sanic import Sanic
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.sanic import setup_doppy
    >>> app = Sanic("demo")
    >>> services = Container()
    >>> setup_doppy(app, services)
"""

from __future__ import annotations

from typing import Any

from doppy_di.container import Container


def setup_doppy(
    app: Any,
    container: Container,
    scope: str = "request",
) -> Any:
    """Register per-request scope middleware on a Sanic app.

    Args:
        app: Sanic application.
        container: Container used for resolution.
        scope: Scope name created per request.

    Returns:
        The same app (middleware registered in place).
    """

    @app.middleware("request")  # type: ignore
    async def _open_doppy_scope(request: Any) -> None:
        s = await container.ascope(scope).__aenter__()
        s.set_context("request", request)
        s.set_context("scope", s)
        request.ctx.doppy_scope = s

    @app.middleware("response")  # type: ignore
    async def _close_doppy_scope(request: Any, _response: Any) -> None:
        s = getattr(request.ctx, "doppy_scope", None)
        if s is not None:
            await s.__aexit__(None, None, None)

    return app
