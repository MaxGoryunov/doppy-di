"""Tests for async-first resolution (issue 81)."""

import asyncio
from typing import Any, AsyncIterator, List, cast

import pytest

from doppy_di import (
    AsyncDependencyInSyncContextError,
    ContainerBuilder,
    ResolutionCancelledError,
    SyncFactoryReturningAwaitableError,
)


def test_aget_resolves_sync_factory() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    assert asyncio.run(container.aget("x")) == 1


def test_aget_resolves_async_factory() -> None:
    async def make() -> int:
        return 42

    builder = ContainerBuilder()
    builder.service("x", make)
    container = builder.build()

    assert asyncio.run(container.aget("x")) == 42


def test_aget_parallel_independent_branches() -> None:
    resolved: List[str] = []

    async def make_a() -> str:
        await asyncio.sleep(0.05)
        resolved.append("a")
        return "A"

    async def make_b() -> str:
        await asyncio.sleep(0.05)
        resolved.append("b")
        return "B"

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    container = builder.build()

    async def main() -> tuple[str, str]:
        return cast(
            tuple[str, str],
            await asyncio.gather(container.aget("a"), container.aget("b")),
        )

    a, b = asyncio.run(main())
    assert a == "A"
    assert b == "B"
    assert set(resolved) == {"a", "b"}


def test_sync_get_async_dependency_raises() -> None:
    async def make_async() -> int:
        return 1

    builder = ContainerBuilder()
    builder.service("a", make_async)
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()

    with pytest.raises(AsyncDependencyInSyncContextError):
        container.get("b")


def test_sync_factory_returning_awaitable_raises() -> None:
    async def inner() -> int:
        return 1

    builder = ContainerBuilder()
    builder.service("a", lambda: inner())
    container = builder.build()

    with pytest.raises(SyncFactoryReturningAwaitableError):
        container.get("a")


def test_aget_cancellation_finalizes_resources() -> None:
    finalized: List[bool] = []

    async def make_resource() -> AsyncIterator[object]:
        try:
            await asyncio.sleep(10)
            yield object()
        finally:
            finalized.append(True)

    builder = ContainerBuilder()
    builder.service("r", make_resource)
    container = builder.build()

    async def main() -> None:
        task = asyncio.create_task(container.aget("r"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert finalized == [True]


def test_aget_async_yield_provider_resolves() -> None:
    async def make_session() -> AsyncIterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    async def main() -> object:
        return await container.aget("session")

    assert asyncio.run(main()) is not None


def test_aget_sync_yield_provider_rejected() -> None:
    def make_session() -> Any:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    with pytest.raises(TypeError):
        asyncio.run(container.aget("session"))


def test_aget_async_depends_on_sync() -> None:
    async def make_async(sync_val: int) -> int:
        return sync_val + 1

    builder = ContainerBuilder()
    builder.value("sync", 1)
    builder.service("async", make_async, deps=["sync"])
    container = builder.build()

    assert asyncio.run(container.aget("async")) == 2


def test_aget_sync_factory_returning_awaitable_raises() -> None:
    async def inner() -> int:
        return 1

    builder = ContainerBuilder()
    builder.service("a", lambda: inner())
    container = builder.build()

    with pytest.raises(SyncFactoryReturningAwaitableError):
        asyncio.run(container.aget("a"))


def test_aget_cancellation_raises_resolution_cancelled() -> None:
    async def make_slow() -> int:
        await asyncio.sleep(10)
        return 1

    builder = ContainerBuilder()
    builder.service("slow", make_slow)
    container = builder.build()

    async def main() -> None:
        task = asyncio.create_task(container.aget("slow"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(ResolutionCancelledError):
            await task

    asyncio.run(main())


def test_aget_async_yield_provider_cached_singleton() -> None:
    calls: List[int] = []

    async def make_session() -> AsyncIterator[object]:
        calls.append(1)
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session, lifetime="singleton")
    container = builder.build()

    async def main() -> tuple[object, object]:
        first = await container.aget("session")
        second = await container.aget("session")
        return first, second

    first, second = asyncio.run(main())
    assert first is second
    assert len(calls) == 1
