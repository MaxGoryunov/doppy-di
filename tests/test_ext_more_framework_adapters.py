"""Tests for the added framework adapters (doppy_di.ext.*).

Real frameworks we can rely on (aiohttp, starlette) are exercised directly;
the rest use lightweight stand-ins injected through ``sys.modules`` so the
adapter bodies run without the third-party library installed.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from doppy_di import Container
from doppy_di.container import ContainerBuilder
from doppy_di.providers import from_context


class _InjectedSvc:
    pass


def _install_fake(monkeypatch: Any, name: str, obj: Any) -> None:
    monkeypatch.setitem(sys.modules, name, obj)


# --------------------------------------------------------------------------- #
# Flask
# --------------------------------------------------------------------------- #


class _FakeFlaskApp:
    def __init__(self) -> None:
        self.before: list[Callable[[], Any]] = []
        self.teardown: list[Callable[[Any], Any]] = []

    def before_request(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        self.before.append(fn)
        return fn

    def teardown_request(self, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        self.teardown.append(fn)
        return fn


def test_flask_opens_and_closes_request_scope(monkeypatch: Any) -> None:
    flask_fake = types.ModuleType("flask")
    flask_fake.g = SimpleNamespace()
    flask_fake.request = object()
    _install_fake(monkeypatch, "flask", flask_fake)

    from doppy_di.ext.flask import setup_doppy

    app = _FakeFlaskApp()
    services = Container()
    services.req = from_context("request")
    setup_doppy(app, services)

    app.before[0]()
    assert flask_fake.g.doppy_scope.get("req") is flask_fake.request
    app.teardown[0](None)


# --------------------------------------------------------------------------- #
# Django
# --------------------------------------------------------------------------- #


def test_django_middleware_creates_request_scope() -> None:
    from doppy_di.ext.django import setup_doppy

    services = Container()
    services.req = from_context("request")
    middleware_cls = setup_doppy(services)
    request = SimpleNamespace()
    seen: list[Any] = []

    def get_response(r: Any) -> str:
        seen.append(r.doppy_scope.get("req"))
        return "ok"

    instance = middleware_cls(get_response)
    result = instance(request)

    assert result == "ok"
    assert seen[0] is request


# --------------------------------------------------------------------------- #
# Starlette
# --------------------------------------------------------------------------- #


class _FakeStarletteApp:
    def __init__(self) -> None:
        self.middlewares: list[Any] = []

    def add_middleware(self, cls: Any) -> None:
        self.middlewares.append(cls)


def test_starlette_registers_middleware() -> None:
    pytest.importorskip("starlette")
    from doppy_di.ext.starlette import setup_doppy

    app = _FakeStarletteApp()
    container = ContainerBuilder().build()
    setup_doppy(cast(Any, app), container)
    assert app.middlewares


# --------------------------------------------------------------------------- #
# Aiohttp
# --------------------------------------------------------------------------- #


class _FakeAiohttpApp:
    def __init__(self) -> None:
        self.middlewares: list[Any] = []


def test_aiohttp_creates_request_scope() -> None:
    pytest.importorskip("aiohttp")
    from doppy_di.ext.aiohttp import setup_doppy

    app = _FakeAiohttpApp()
    services = Container()
    services.req = from_context("request")
    setup_doppy(app, services)

    middleware = app.middlewares[0]
    request: dict[str, Any] = {}
    seen: list[Any] = []

    async def handler(r: Any) -> str:
        seen.append(await r["doppy_scope"].get("req"))
        return "ok"

    result = asyncio.run(middleware(request, handler))
    assert result == "ok"
    assert seen[0] is request


# --------------------------------------------------------------------------- #
# Sanic
# --------------------------------------------------------------------------- #


class _FakeSanicApp:
    def __init__(self) -> None:
        self.request_mw: list[Callable[[Any], Any]] = []
        self.response_mw: list[Callable[[Any, Any], Any]] = []

    def middleware(self, kind: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            if kind == "request":
                self.request_mw.append(fn)
            else:
                self.response_mw.append(fn)
            return fn

        return decorate


def test_sanic_creates_request_scope() -> None:
    from doppy_di.ext.sanic import setup_doppy

    app = _FakeSanicApp()
    services = Container()
    services.req = from_context("request")
    setup_doppy(app, services)

    request = SimpleNamespace(ctx=SimpleNamespace())

    async def main() -> None:
        await app.request_mw[0](request)
        assert await request.ctx.doppy_scope.get("req") is request
        await app.response_mw[0](request, object())

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# Litestar
# --------------------------------------------------------------------------- #


def test_litestar_wires_hooks_on_instance(monkeypatch: Any) -> None:
    litestar_fake = types.ModuleType("litestar")
    litestar_fake.Litestar = type("Litestar", (), {})
    _install_fake(monkeypatch, "litestar", litestar_fake)

    from doppy_di.ext.litestar import setup_doppy

    services = Container()
    services.req = from_context("request")
    app = SimpleNamespace()
    setup_doppy(app, services)

    request = SimpleNamespace(scope={})

    async def main() -> None:
        await app.on_before_request(request)
        scope_obj = request.scope["doppy_scope"]
        assert await scope_obj.get("req") is request
        assert await app.on_after_request(request, "resp") == "resp"

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# Celery
# --------------------------------------------------------------------------- #


class _FakeSignal:
    def __init__(self) -> None:
        self.handlers: list[Callable[..., Any]] = []

    def connect(self, fn: Callable[..., Any], weak: bool = False) -> None:
        self.handlers.append(fn)


def test_celery_opens_per_task_scope(monkeypatch: Any) -> None:
    signals = SimpleNamespace(
        task_prerun=_FakeSignal(),
        task_postrun=_FakeSignal(),
    )
    celery_fake = types.ModuleType("celery")
    celery_fake.signals = signals
    _install_fake(monkeypatch, "celery", celery_fake)

    from doppy_di.ext.celery import setup_doppy

    services = Container()
    services.task = from_context("task_id")
    app = object()
    setup_doppy(app, services)

    signals.task_prerun.handlers[0](task_id="a")
    scope_obj = services.scope("task")
    assert scope_obj.get("task") == "a"
    signals.task_postrun.handlers[0](task_id="a")


# --------------------------------------------------------------------------- #
# Taskiq
# --------------------------------------------------------------------------- #


def test_taskiq_creates_per_task_scope(monkeypatch: Any) -> None:
    class Middleware:
        async def pre_execute(self, message: Any, *args: Any, **kwargs: Any) -> Any:
            return None

        async def post_execute(
            self,
            message: Any,
            result: Any,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            return result

    taskiq_fake = types.ModuleType("taskiq")
    middleware_pkg = types.ModuleType("taskiq.middlewares")
    middleware_mod = types.ModuleType("taskiq.middlewares.middleware")
    middleware_mod.Middleware = Middleware
    middleware_pkg.middleware = middleware_mod
    taskiq_fake.middlewares = middleware_pkg
    _install_fake(monkeypatch, "taskiq", taskiq_fake)
    _install_fake(monkeypatch, "taskiq.middlewares", middleware_pkg)
    _install_fake(monkeypatch, "taskiq.middlewares.middleware", middleware_mod)

    from doppy_di.ext.taskiq import setup_doppy

    broker = SimpleNamespace(middlewares=[])

    def add_middleware(mw: Any) -> None:
        broker.middlewares.append(mw)

    broker.add_middleware = add_middleware  # type: ignore[attr-defined]
    services = Container()
    services.task = from_context("task")
    setup_doppy(broker, services)

    message = object()
    mw = broker.middlewares[0]

    async def main() -> None:
        await mw.pre_execute(message)
        assert await mw._scope.get("task") is message
        assert await mw.post_execute(message, "ok") == "ok"
        assert mw._scope is None

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# FastStream
# --------------------------------------------------------------------------- #


def test_faststream_wires_hooks_on_app_class(monkeypatch: Any) -> None:
    class _FakeFastStream:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    faststream_fake = types.ModuleType("faststream")
    faststream_fake.FastStream = _FakeFastStream
    _install_fake(monkeypatch, "faststream", faststream_fake)

    from doppy_di.ext.faststream import setup_doppy

    services = Container()
    services.msg = from_context("message")

    app_cls = faststream_fake.FastStream
    app = setup_doppy(app_cls, services)
    message = object()

    async def main() -> None:
        await app.before_handle(message)
        assert await app.doppy_manager._scope.get("msg") is message  # type: ignore[attr-defined]
        assert await app.after_handle(message, "ok") == "ok"

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# gRPC
# --------------------------------------------------------------------------- #


def test_grpc_interceptor_opens_per_rpc_scope(monkeypatch: Any) -> None:
    grpc_fake = types.ModuleType("grpc")
    grpc_fake.ServerInterceptor = type("ServerInterceptor", (), {})
    _install_fake(monkeypatch, "grpc", grpc_fake)

    from doppy_di.ext.grpc import setup_doppy

    services = Container()
    services.rpc = from_context("rpc")
    interceptor = setup_doppy(services)

    seen: list[Any] = []

    def continuation(request: Any, context: Any) -> str:
        seen.append(services.scope("rpc").get("rpc"))
        return "handled"

    handler = interceptor.intercept_service(continuation, "rpc-details")
    assert handler("req", "ctx") == "handled"
    assert seen[0] == "rpc-details"


def test_pass_exempts_framework_supplied_param() -> None:
    from doppy_di.inject import Depends, Pass, inject

    builder = ContainerBuilder()
    svc = _InjectedSvc()
    builder.value(_InjectedSvc, svc)
    container = builder.build()

    @inject(container=container)
    def handler(
        service: Any = Depends(_InjectedSvc),  # noqa: B008
        request: object = Pass(),
    ) -> tuple[Any, object]:
        return service, request

    framework_req = object()
    assert handler(request=framework_req) == (svc, framework_req)
