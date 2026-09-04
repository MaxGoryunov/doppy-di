"""FastStream integration.

Provides ``setup_doppy`` which registers middleware that opens a fresh scope
per message.

Examples:
    >>> from faststream import FastStream
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.faststream import setup_doppy
    >>> services = Container()
    >>> setup_doppy(FastStream, services)
"""

from __future__ import annotations

from typing import Any

from doppy_di.container import Container


def setup_doppy(
    faststream_app: Any,
    container: Container,
    scope: str = "message",
) -> Any:
    """Return a FastStream app configured with per-message scopes.

    Args:
        faststream_app: FastStream application class or instance.
        container: Container used for resolution.
        scope: Scope name created per message.

    Returns:
        A FastStream application with the scope middleware.
    """
    from faststream import FastStream  # type: ignore[import-not-found]

    class _ScopeManager:
        def __init__(self) -> None:
            self._scope: Any = None

        async def before(self, msg: Any) -> None:
            scope_obj = await container.ascope(scope).__aenter__()
            scope_obj.set_context("message", msg)
            self._scope = scope_obj

        async def after(self, _msg: Any, result: Any) -> Any:
            scope_obj, self._scope = self._scope, None
            if scope_obj is not None:
                await scope_obj.__aexit__(None, None, None)
            return result

    manager = _ScopeManager()

    async def _before(msg: Any) -> None:
        await manager.before(msg)

    async def _after(msg: Any, result: Any) -> Any:
        return await manager.after(msg, result)

    if isinstance(faststream_app, type) and issubclass(faststream_app, FastStream):
        instance = faststream_app(
            before_handle=_before,
            after_handle=_after,
        )
        instance.doppy_manager = manager
        return instance
    faststream_app.before_handle = _before
    faststream_app.after_handle = _after
    faststream_app.doppy_manager = manager
    return faststream_app
