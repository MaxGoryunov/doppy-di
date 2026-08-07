"""Tests for yield-provider generators with scope finalization."""

import asyncio
from typing import Any, AsyncIterator, Iterator, List

import pytest

from doppy_di import ContainerBuilder, YieldNotCalledError


def test_sync_yield_provider_finalized_on_scope_exit() -> None:
    closed: List[bool] = []

    def make_session() -> Iterator[object]:
        try:
            yield object()
        finally:
            closed.append(True)

    builder = ContainerBuilder()
    builder.service("session", make_session, lifetime="transient")
    container = builder.build()

    with container.scope("req") as scope:
        session = scope.get("session")
        assert session is not None
        assert closed == []

    assert closed == [True]


def test_async_yield_provider_finalized_on_scope_exit() -> None:
    closed: List[bool] = []

    async def make_session() -> AsyncIterator[object]:
        try:
            yield object()
        finally:
            closed.append(True)

    builder = ContainerBuilder()
    builder.service("session", make_session, lifetime="transient")
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            session = await scope.get("session")
            assert session is not None
            assert closed == []

    asyncio.run(main())
    assert closed == [True]


def test_no_yield_provider_no_exit_stack() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with container.scope("req") as scope:
        scope.get("x")

    assert scope._exit_stack == []


def test_finalization_error_logged_not_raised(caplog: Any) -> None:
    def make_bad() -> Iterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder()
    builder.service("bad", make_bad)
    container = builder.build()

    with container.scope("req") as scope:
        scope.get("bad")

    assert "Error finalizing yield provider" in caplog.text


def test_yield_not_called_raises() -> None:
    def make_empty() -> Iterator[object]:
        if False:
            yield object()  # type: ignore[unreachable]

    builder = ContainerBuilder()
    builder.service("empty", make_empty)
    container = builder.build()

    with container.scope("req") as scope, pytest.raises(YieldNotCalledError):
        scope.get("empty")


def test_async_yield_provider_requires_async_scope() -> None:
    async def make_session() -> AsyncIterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    with pytest.raises(TypeError):
        container.get("session")


def test_async_scope_rejects_sync_yield_provider() -> None:
    def make_session() -> Iterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            with pytest.raises(TypeError):
                await scope.get("session")

    asyncio.run(main())


def test_async_scope_reuses_same_instance() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    assert container.ascope("req") is container.ascope("req")


def test_ascope_rejects_existing_sync_scope() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    container.scope("req")
    with pytest.raises(TypeError):
        container.ascope("req")


def test_ascope_unique_policy_returns_fresh_scope() -> None:
    from doppy_di import ScopePolicy

    builder = ContainerBuilder(scope_policy=ScopePolicy.UNIQUE)
    builder.value("x", 1)
    container = builder.build()

    assert container.ascope("req") is not container.ascope("req")


def test_async_finalization_error_logged_not_raised(caplog: Any) -> None:
    async def make_bad() -> AsyncIterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder()
    builder.service("bad", make_bad)
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            await scope.get("bad")

    asyncio.run(main())
    assert "Error finalizing yield provider" in caplog.text


def test_sync_yield_provider_cached_in_scope() -> None:
    def make_session() -> Iterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    with container.scope("req") as scope:
        a = scope.get("session")
        b = scope.get("session")
        assert a is b


def test_async_yield_provider_cached_in_scope() -> None:
    async def make_session() -> AsyncIterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            a = await scope.get("session")
            b = await scope.get("session")
            assert a is b

    asyncio.run(main())
