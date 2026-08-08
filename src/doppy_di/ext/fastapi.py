"""FastAPI integration.

Provides ``setup_doppy`` which registers middleware that creates a
per-request scope and exposes it via ``request.state``.

Example:
    >>> from fastapi import FastAPI
    >>> from doppy_di.container import ContainerBuilder
    >>> from doppy_di.ext.fastapi import setup_doppy
    >>> app = FastAPI()
    >>> container = ContainerBuilder().build()
    >>> setup_doppy(app, container)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from doppy_di.container import Container

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response


def setup_doppy(
    app: FastAPI,
    container: Container,
    scope: str = "request",
) -> None:
    """Register request-scope middleware on a FastAPI app.

    Args:
        app: FastAPI application.
        container: Container used for resolution.
        scope: Scope name created per request.
    """

    @app.middleware("http")
    async def _doppy_scope_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        async with container.ascope(scope) as s:
            request.state.doppy_scope = s
            return await call_next(request)
