"""Tests for parallel async dependency resolution."""

import asyncio
from typing import Any, List, Tuple

import pytest

from doppy_di import ContainerBuilder, injectable


def test_parallel_resolves_independent_dependencies() -> None:
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

    async def main() -> List[Any]:
        return await container.get_many(["a", "b"], parallel=True)

    a, b = asyncio.run(main())
    assert a == "A"
    assert b == "B"
    assert set(resolved) == {"a", "b"}


def test_parallel_respects_dependencies() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda: "A")
    builder.service("b", lambda a: a + "B", deps=["a"])
    container = builder.build()

    async def main() -> List[Any]:
        return await container.get_many(["a", "b"], parallel=True)

    a, b = asyncio.run(main())
    assert a == "A"
    assert b == "AB"


def test_sync_container_no_parallelism() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    container = builder.build()

    assert container.get("a") == 1
    assert container.get("b") == 2


def test_parallel_cancels_on_failure() -> None:
    async def make_bad() -> Any:
        raise RuntimeError("boom")

    builder = ContainerBuilder()
    builder.service("bad", make_bad)
    builder.value("other", 1)
    container = builder.build()

    async def main() -> None:
        await container.get_many(["bad", "other"], parallel=True)

    with pytest.raises(RuntimeError):
        asyncio.run(main())


def test_aget_resolves_async_factory() -> None:
    async def make_value() -> int:
        await asyncio.sleep(0.01)
        return 42

    builder = ContainerBuilder()
    builder.service("answer", make_value)
    container = builder.build()

    assert asyncio.run(container.aget("answer")) == 42


def test_aget_caches_singleton() -> None:
    calls: List[int] = []

    async def make_value() -> object:
        calls.append(1)
        return object()

    builder = ContainerBuilder()
    builder.service("obj", make_value, lifetime="singleton")
    container = builder.build()

    async def main() -> Tuple[object, object]:
        first = await container.aget("obj")
        second = await container.aget("obj")
        return first, second

    first, second = asyncio.run(main())
    assert first is second
    assert len(calls) == 1


def test_get_many_sequential_default() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    container = builder.build()

    assert asyncio.run(container.get_many(["a", "b"])) == [1, 2]


def test_parallel_small_graph_sequential_fallback() -> None:
    resolved: List[str] = []

    async def make_a() -> str:
        await asyncio.sleep(0.01)
        resolved.append("a")
        return "A"

    async def make_b() -> str:
        await asyncio.sleep(0.01)
        resolved.append("b")
        return "B"

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    container = builder.build()

    async def main() -> List[Any]:
        return await container.get_many(["a", "b"], parallel=True)

    a, b = asyncio.run(main())
    assert a == "A"
    assert b == "B"
    assert set(resolved) == {"a", "b"}


def test_parallel_dedups_shared_dependency() -> None:
    calls: List[int] = []

    async def make_shared() -> str:
        calls.append(1)
        await asyncio.sleep(0.01)
        return "S"

    async def make_x(shared: str) -> str:
        return shared + "X"

    async def make_y(shared: str) -> str:
        return shared + "Y"

    builder = ContainerBuilder()
    builder.service("shared", make_shared, lifetime="singleton")
    builder.service("x", make_x, deps=["shared"])
    builder.service("y", make_y, deps=["shared"])
    container = builder.build()

    async def main() -> List[Any]:
        return await container.get_many(["x", "y"], parallel=True)

    x, y = asyncio.run(main())
    assert x == "SX"
    assert y == "SY"
    assert len(calls) == 1


def test_parallel_large_graph_resolves_concurrently() -> None:
    resolved: List[str] = []

    async def make_a() -> str:
        await asyncio.sleep(0.05)
        resolved.append("a")
        return "A"

    async def make_b() -> str:
        await asyncio.sleep(0.05)
        resolved.append("b")
        return "B"

    async def make_c() -> str:
        await asyncio.sleep(0.05)
        resolved.append("c")
        return "C"

    async def make_d() -> str:
        await asyncio.sleep(0.05)
        resolved.append("d")
        return "D"

    async def make_e() -> str:
        await asyncio.sleep(0.05)
        resolved.append("e")
        return "E"

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    builder.service("c", make_c)
    builder.service("d", make_d)
    builder.service("e", make_e)
    container = builder.build()

    async def main() -> Tuple[List[str], float]:
        start = asyncio.get_event_loop().time()
        results = await container.get_many(["a", "b", "c", "d", "e"], parallel=True)
        elapsed = asyncio.get_event_loop().time() - start
        return results, elapsed

    results, elapsed = asyncio.run(main())
    assert results == ["A", "B", "C", "D", "E"]
    assert set(resolved) == {"a", "b", "c", "d", "e"}
    # 5 nodes resolved concurrently, not 5 * 0.05s
    assert elapsed < 0.2


def test_aget_missing_key_raises() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(KeyError):
        asyncio.run(container.aget("missing"))


def test_aget_async_yield_provider_resolves() -> None:
    async def make_session() -> Any:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    assert asyncio.run(container.aget("session")) is not None


def test_aget_auto_wires_injectable_class() -> None:
    @injectable
    class Service:
        pass

    builder = ContainerBuilder()
    container = builder.build()

    result = asyncio.run(container.aget(Service))
    assert isinstance(result, Service)
