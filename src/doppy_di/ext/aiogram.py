"""aiogram integration.

Provides ``setup_doppy`` which registers middleware that creates a
per-update scope and exposes it via the update data dict.

Examples:
    >>> from aiogram import Bot
    >>> from doppy_di.container import ContainerBuilder
    >>> from doppy_di.ext.aiogram import setup_doppy
    >>> bot = Bot(token="test")
    >>> container = ContainerBuilder().build()
    >>> setup_doppy(bot, container)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from doppy_di.container import Container


class _ScopeMiddleware:
    """Middleware that creates a scope per update."""

    def __init__(self, container: Container, scope: str) -> None:
        self.container = container
        self.scope = scope

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self.container.ascope(self.scope) as s:
            data["doppy_scope"] = s
            return await handler(event, data)


def setup_doppy(
    bot: Any,
    container: Container,
    scope: str = "update",
) -> None:
    """Register update-scope middleware on an aiogram bot.

    Args:
        bot: aiogram Bot instance.
        container: Container used for resolution.
        scope: Scope name created per update.
    """
    bot.session.middleware(_ScopeMiddleware(container, scope))
