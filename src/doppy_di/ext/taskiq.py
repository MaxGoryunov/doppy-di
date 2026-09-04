"""Taskiq integration.

Provides ``setup_doppy`` which registers middleware that opens a per-task
scope.

Examples:
    >>> from taskiq import InMemoryBroker
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.taskiq import setup_doppy
    >>> broker = InMemoryBroker()
    >>> services = Container()
    >>> setup_doppy(broker, services)
"""

from __future__ import annotations

from typing import Any

from doppy_di.container import Container


def setup_doppy(
    broker: Any,
    container: Container,
    scope: str = "task",
) -> Any:
    """Register per-task scope middleware on a Taskiq broker.

    Args:
        broker: Taskiq broker.
        container: Container used for resolution.
        scope: Scope name created per task.

    Returns:
        The same broker (middleware registered in place).
    """
    from taskiq.middlewares.middleware import Middleware  # type: ignore[import-not-found]

    class _DoppyMiddleware(Middleware):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self._scope: Any = None

        async def pre_execute(self, message: Any, *args: Any, **kwargs: Any) -> Any:
            scope_obj = await container.ascope(scope).__aenter__()
            scope_obj.set_context("task", message)
            self._scope = scope_obj
            return await super().pre_execute(message, *args, **kwargs)

        async def post_execute(
            self,
            message: Any,
            result: Any,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            scope_obj, self._scope = self._scope, None
            if scope_obj is not None:
                await scope_obj.__aexit__(None, None, None)
            return await super().post_execute(message, result, *args, **kwargs)

    broker.add_middleware(_DoppyMiddleware())
    return broker
