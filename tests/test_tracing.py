"""Tests for the tracing feature (issue 33)."""

import asyncio
from typing import Any, Callable, List, Optional, Tuple

from doppy_di.container import ContainerBuilder

Event = Tuple[Any, float, bool, Optional[str]]
Tracer = Callable[[Any, float, bool, Optional[str]], None]


def _collect(events: List[Event]) -> Tracer:
    def tracer(key: Any, duration: float, cache_hit: bool, scope: Optional[str]) -> None:
        events.append((key, duration, cache_hit, scope))

    return tracer


def test_tracer_receives_events() -> None:
    events: List[Any] = []

    def tracer(key: Any, duration: float, cache_hit: bool, scope: Optional[str]) -> None:
        events.append((key, cache_hit, scope))

    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(tracer)

    container.get("a")
    container.get("a")  # cache hit

    first, second = events
    assert first[0] == "a"
    assert first[1] is False  # miss
    assert second[1] is True  # hit


def test_tracer_receives_duration() -> None:
    events: List[float] = []

    def tracer(key: Any, duration: float, cache_hit: bool, scope: Optional[str]) -> None:
        events.append(duration)

    builder = ContainerBuilder()
    builder.service("a", lambda: 1)
    container = builder.build()
    container.set_tracer(tracer)

    container.get("a")
    assert events[0] >= 0


def test_no_tracer_no_overhead() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    # no tracer set: get() works, no events
    assert container.get("a") == 1


def test_remove_tracer() -> None:
    events: List[Any] = []

    def tracer(key: Any, duration: float, cache_hit: bool, scope: Optional[str]) -> None:
        events.append(key)

    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(tracer)
    container.set_tracer(None)

    container.get("a")
    assert events == []


def test_trace_transient_service_miss() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.service("a", lambda: 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    container.get("a")

    assert events[0][0] == "a"
    assert events[0][1] >= 0
    assert events[0][2] is False
    assert events[0][3] is None


def test_trace_singleton_first_miss_then_hit() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.service("a", lambda: object(), lifetime="singleton")
    container = builder.build()
    container.set_tracer(_collect(events))

    container.get("a")
    container.get("a")

    assert events[0][2] is False
    assert events[1][2] is True


def test_trace_override_sync() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    with container.override("a", 2):
        assert container.get("a") == 2

    assert len(events) == 1
    assert events[0][0] == "a"
    assert events[0][2] is False
    assert events[0][3] is None


def test_trace_scope_name_propagates() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    with container.scope("req") as scope:
        scope.get("a")

    assert events[0][3] == "req"


def test_trace_scope_cache_hit() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    with container.scope("req") as scope:
        scope.get("a")
        scope.get("a")

    # first: container miss traced with scope name; second: scope cache hit
    assert len(events) == 2
    assert events[0][0] == "a"
    assert events[0][2] is False
    assert events[0][3] == "req"
    assert events[1][2] is True
    assert events[1][3] == "req"


def test_trace_scope_yield_provider() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()

    def make() -> Any:
        yield "resource"

    builder.service("res", make)
    container = builder.build()
    container.set_tracer(_collect(events))

    with container.scope("s") as scope:
        assert scope.get("res") == "resource"

    assert len(events) == 1
    assert events[0][0] == "res"
    assert events[0][2] is False
    assert events[0][3] == "s"


def test_trace_async_cache_hit() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.service("a", lambda: 1, lifetime="singleton")
    container = builder.build()
    container.set_tracer(_collect(events))

    async def main() -> None:
        await container.aget("a")
        await container.aget("a")

    asyncio.run(main())

    assert len(events) == 2
    assert events[0][2] is False
    assert events[1][2] is True


def test_trace_async_override() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    async def main() -> Any:
        with container.override("a", 2):
            return await container.aget("a")

    assert asyncio.run(main()) == 2

    assert len(events) == 1
    assert events[0][0] == "a"
    assert events[0][2] is False


def test_trace_async_yield_provider() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()

    async def make() -> Any:
        yield "resource"

    builder.service("res", make)
    container = builder.build()
    container.set_tracer(_collect(events))

    async def main() -> Any:
        return await container.aget("res")

    assert asyncio.run(main()) == "resource"

    assert len(events) == 1
    assert events[0][0] == "res"
    assert events[0][2] is False
    assert events[0][3] is None


def test_trace_async_scope_hit() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))

    async def main() -> None:
        async with container.ascope("s") as scope:
            await scope.get("a")
            await scope.get("a")

    asyncio.run(main())

    assert len(events) == 2
    assert events[0][2] is False
    assert events[0][3] == "s"
    assert events[1][2] is True
    assert events[1][3] == "s"


def test_trace_async_scope_yield_provider() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()

    async def make() -> Any:
        yield "resource"

    builder.service("res", make)
    container = builder.build()
    container.set_tracer(_collect(events))

    async def main() -> Any:
        async with container.ascope("s") as scope:
            return await scope.get("res")

    assert asyncio.run(main()) == "resource"

    assert len(events) == 1
    assert events[0][0] == "res"
    assert events[0][2] is False
    assert events[0][3] == "s"


def test_child_inherits_tracer() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    parent = builder.build()
    parent.set_tracer(_collect(events))
    child = parent.child()
    child.value("b", 2)

    child.get("b")

    assert len(events) == 1
    assert events[0][0] == "b"
    assert events[0][2] is False


def test_qualified_key_traced() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.service("a", lambda: 1, qualifier="read")
    container = builder.build()
    container.set_tracer(_collect(events))

    container.get("a", qualifier="read")

    assert events[0][0] == ("a", "read")


def test_plan_delegates_to_traced_container() -> None:
    events: List[Event] = []
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(_collect(events))
    plan = container.compile()

    plan.get("a")

    assert len(events) == 1
    assert events[0][0] == "a"
