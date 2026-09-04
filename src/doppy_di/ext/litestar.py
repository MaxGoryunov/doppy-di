"""Litestar integration.

Provides ``setup_doppy`` which registers request lifecycle hooks that create a
per-request scope, exposed via ``request.scope`` state.

Examples:
    >>> from litestar import Litestar
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.litestar import setup_doppy
    >>> services = Container()
    >>> setup_doppy(Litestar, services)
"""

from __future__ import annotations

from typing import Any

from doppy_di.container import Container


def setup_doppy(
    litestar_app: Any,
    container: Container,
    scope: str = "request",
) -> Any:
    """Create a Litestar app class/instance wired with request scopes.

    Args:
        litestar_app: Litestar application class or instance.
        container: Container used for resolution.
        scope: Scope name created per request.

    Returns:
        A Litestar application configured with the hooks.
    """
    from litestar import Litestar  # type: ignore[import-not-found]

    async def _before_request(request: Any, _container: Container = container) -> Any:
        s = await _container.ascope(scope).__aenter__()
        s.set_context("request", request)
        s.set_context("scope", s)
        request.scope["doppy_scope"] = s
        return None

    async def _after_request(request: Any, response: Any) -> Any:
        s = request.scope.get("doppy_scope")
        if s is not None:
            await s.__aexit__(None, None, None)
        return response

    if isinstance(litestar_app, type) and issubclass(litestar_app, Litestar):
        return litestar_app(
            on_before_request=_before_request,
            on_after_request=_after_request,
        )
    litestar_app.on_before_request = _before_request
    litestar_app.on_after_request = _after_request
    return litestar_app
