"""Tests for optional framework integrations (doppy_di.ext.*)."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from doppy_di.container import ContainerBuilder

_Middleware = Callable[..., Any]


class _FakeApp:
    """Minimal FastAPI stand-in for middleware registration."""

    def __init__(self) -> None:
        self.middlewares: list[_Middleware] = []

    def middleware(self, _kind: str) -> Callable[[_Middleware], _Middleware]:
        def decorate(func: _Middleware) -> _Middleware:
            self.middlewares.append(func)
            return func

        return decorate


class _FakeSession:
    """Minimal aiogram session stand-in."""

    def __init__(self) -> None:
        self.middlewares: list[Any] = []

    def middleware(self, mw: Any) -> None:
        self.middlewares.append(mw)


class _FakeBot:
    """Minimal aiogram Bot stand-in."""

    def __init__(self) -> None:
        self.session = _FakeSession()


class _FakeCommand:
    """Minimal Typer command stand-in."""

    def __init__(self, callback: Callable[..., Any] | None) -> None:
        self.callback = callback


class _FakeTyper:
    """Minimal Typer app stand-in."""

    def __init__(self) -> None:
        self.registered_commands: list[_FakeCommand] = []


def test_ext_not_imported_by_default() -> None:
    import doppy_di

    assert "doppy_di.ext" not in sys.modules
    assert doppy_di.__all__


def test_fastapi_setup_adds_middleware() -> None:
    from doppy_di.ext.fastapi import setup_doppy

    app = _FakeApp()
    container = ContainerBuilder().build()

    setup_doppy(cast(Any, app), container)

    assert len(app.middlewares) == 1


def test_fastapi_middleware_creates_request_scope() -> None:
    from doppy_di.ext.fastapi import setup_doppy

    app = _FakeApp()
    builder = ContainerBuilder()
    builder.value("db", object())
    container = builder.build()
    setup_doppy(cast(Any, app), container)

    middleware = app.middlewares[0]
    request = SimpleNamespace(state=SimpleNamespace())
    called: list[Any] = []

    async def call_next(_request: Any) -> str:
        called.append(_request.state.doppy_scope)
        return "ok"

    result = asyncio.run(middleware(request, call_next))

    assert result == "ok"
    assert len(called) == 1
    assert asyncio.run(called[0].get("db")) is container.get("db")


def test_aiogram_setup_adds_middleware() -> None:
    from doppy_di.ext.aiogram import setup_doppy

    bot = _FakeBot()
    container = ContainerBuilder().build()

    setup_doppy(bot, container)

    assert len(bot.session.middlewares) == 1


def test_aiogram_middleware_creates_update_scope() -> None:
    from doppy_di.ext.aiogram import setup_doppy

    bot = _FakeBot()
    builder = ContainerBuilder()
    builder.value("db", object())
    container = builder.build()
    setup_doppy(bot, container)

    middleware = bot.session.middlewares[0]
    data: dict[str, Any] = {}
    seen: list[Any] = []

    async def handler(_event: Any, d: dict[str, Any]) -> str:
        seen.append(d["doppy_scope"])
        return "ok"

    result = asyncio.run(middleware(handler, object(), data))

    assert result == "ok"
    assert len(seen) == 1
    assert asyncio.run(seen[0].get("db")) is container.get("db")


def test_typer_setup_wraps_commands() -> None:
    from doppy_di.ext.typer import setup_doppy

    app = _FakeTyper()
    app.registered_commands.append(_FakeCommand(lambda: None))
    container = ContainerBuilder().build()

    setup_doppy(app, container)

    assert hasattr(app.registered_commands[0].callback, "__wrapped__")


def test_typer_setup_skips_none_callback() -> None:
    from doppy_di.ext.typer import setup_doppy

    app = _FakeTyper()
    app.registered_commands.append(_FakeCommand(None))
    container = ContainerBuilder().build()

    setup_doppy(app, container)

    assert app.registered_commands[0].callback is None


def test_fastapi_real_app() -> None:
    fastapi = pytest.importorskip("fastapi")

    from doppy_di.ext.fastapi import setup_doppy

    app = fastapi.FastAPI()
    container = ContainerBuilder().build()

    setup_doppy(app, container)

    assert len(app.user_middleware) > 0


def test_aiogram_real_bot() -> None:
    aiogram = pytest.importorskip("aiogram")

    from doppy_di.ext.aiogram import setup_doppy

    bot = aiogram.Bot(token="1234567890:AAFakeTokenForTesting")
    container = ContainerBuilder().build()

    setup_doppy(bot, container)

    assert len(bot.session.middleware._middlewares) > 0


def test_typer_real_app() -> None:
    typer = pytest.importorskip("typer")

    from doppy_di.ext.typer import setup_doppy

    app = typer.Typer()
    container = ContainerBuilder().build()

    setup_doppy(app, container)

    assert app.registered_commands == []
