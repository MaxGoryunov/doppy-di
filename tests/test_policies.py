"""Tests for pluggable resolution policies."""

import asyncio
from typing import Any, List

import pytest

from doppy_di import (
    AsyncDependencyInSyncContextError,
    ContainerBuilder,
    EagerPolicy,
    LazyPolicy,
    ParallelPolicy,
    ResolutionChildrenFirstPolicy,
    ResolutionParentFirstPolicy,
    ResolutionPolicy,
)
from doppy_di.container import Rule, RuleSet
from doppy_di.resolution import DefaultResolutionPolicy


def test_default_policy_unchanged() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    assert container.get("a") == 1


def test_children_first_policy_order() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionChildrenFirstPolicy())

    assert container.get("a") == 1


def test_parent_first_policy_order() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionParentFirstPolicy())

    assert container.get("a") == 1


def test_lazy_policy_defers_resolution() -> None:
    calls: List[str] = []

    def make_a() -> int:
        calls.append("a")
        return 1

    def make_b() -> int:
        calls.append("b")
        return 2

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    container = builder.build(policy=LazyPolicy())

    assert calls == []
    assert container.get("a") == 1
    assert "b" not in calls


def test_lazy_policy_order_returns_root() -> None:
    assert list(LazyPolicy().order(RuleSet().map, "a")) == ["a"]


def test_default_resolution_policy_order_returns_root() -> None:
    assert list(DefaultResolutionPolicy().order({}, "a")) == ["a"]


def test_custom_policy() -> None:
    class ReversePolicy:
        def order(self, graph: Any, root: Any) -> List[Any]:
            return list(reversed(list(graph.keys())))

    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build(policy=ReversePolicy())

    assert container.get("a") == 1


def test_custom_protocol_isinstance() -> None:
    class ReversePolicy:
        def order(self, graph: Any, root: Any) -> List[Any]:
            return list(reversed(list(graph.keys())))

    assert isinstance(ReversePolicy(), ResolutionPolicy)


def test_policy_per_get_call() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    assert container.get("a", policy=EagerPolicy()) == 1


def test_policy_per_aget_call() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    assert asyncio.run(container.aget("a", policy=ParallelPolicy())) == 1


def test_eager_policy_resolves_at_build() -> None:
    resolved: List[str] = []

    def make_a() -> int:
        resolved.append("a")
        return 1

    def make_b() -> int:
        resolved.append("b")
        return 2

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    container = builder.build(policy=EagerPolicy())

    assert set(resolved) == {"a", "b"}
    assert container.get("a") == 1
    assert container.get("b") == 2


def test_eager_policy_raises_on_async_rule() -> None:
    async def make_async() -> int:
        return 1

    builder = ContainerBuilder()
    builder.service("a", make_async)
    builder.service("b", lambda: 2)

    with pytest.raises(AsyncDependencyInSyncContextError):
        builder.build(policy=EagerPolicy())


def test_parallel_policy_sync_get_sequential() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build(policy=ParallelPolicy())

    assert container.get("b") == 2


def test_parallel_policy_aget_resolves() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build(policy=ParallelPolicy())

    assert asyncio.run(container.aget("b")) == 2


def test_parallel_policy_aget_concurrent() -> None:
    resolved: List[str] = []

    async def make_a() -> str:
        await asyncio.sleep(0.05)
        resolved.append("a")
        return "A"

    async def make_b() -> str:
        await asyncio.sleep(0.05)
        resolved.append("b")
        return "B"

    async def make_c(a: str, b: str) -> str:
        return a + b

    builder = ContainerBuilder()
    builder.service("a", make_a)
    builder.service("b", make_b)
    builder.service("c", make_c, deps=["a", "b"])
    container = builder.build(policy=ParallelPolicy())

    assert asyncio.run(container.aget("c")) == "AB"
    assert set(resolved) == {"a", "b"}


def test_children_first_resolves_deps_first() -> None:
    order: List[str] = []

    def make_b() -> int:
        order.append("b")
        return 1

    def make_a(b: int) -> int:
        order.append("a")
        return b

    builder = ContainerBuilder()
    builder.service("b", make_b, lifetime="singleton")
    builder.service("a", make_a, deps=["b"], lifetime="singleton")
    container = builder.build(policy=ResolutionChildrenFirstPolicy())

    assert container.get("a") == 1
    assert order == ["b", "a"]


def test_parent_first_resolves_parent_first() -> None:
    order: List[str] = []

    def make_b() -> int:
        order.append("b")
        return 1

    def make_a(b: int) -> int:
        order.append("a")
        return b

    builder = ContainerBuilder()
    builder.service("b", make_b, lifetime="singleton")
    builder.service("a", make_a, deps=["b"], lifetime="singleton")
    container = builder.build(policy=ResolutionParentFirstPolicy())

    assert container.get("a") == 1
    assert order == ["b", "a"]


def test_children_first_only_root_reachable() -> None:
    builder = ContainerBuilder()
    builder.value("unused", 1)
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionChildrenFirstPolicy())

    assert container.get("a") == 1


def test_parent_first_only_root_reachable() -> None:
    builder = ContainerBuilder()
    builder.value("unused", 1)
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionParentFirstPolicy())

    assert container.get("a") == 1


def test_policy_with_missing_dependency_keeps_error() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionParentFirstPolicy())

    with pytest.raises(KeyError):
        container.get("a")


def test_policy_child_inherits_container_policy() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    parent = builder.build(policy=EagerPolicy())

    child = parent.child()
    assert child.get("a") == 1


def test_policy_dispatches_for_lazy_get() -> None:
    resolved: List[str] = []

    def make_a() -> int:
        resolved.append("a")
        return 1

    builder = ContainerBuilder()
    builder.service("a", make_a)
    container = builder.build(policy=LazyPolicy())

    assert container.get("a") == 1
    assert resolved == ["a"]


def test_resolution_policy_order_reachable_helpers() -> None:
    rules = RuleSet()
    rules.add("b", Rule("b", lambda: 1))
    rules.add("a", Rule("a", lambda b: b, deps=("b",)))

    parent = ResolutionParentFirstPolicy()
    order = list(parent.order(rules.map, "a"))
    assert order.index("a") < order.index("b")

    children = ResolutionChildrenFirstPolicy()
    order = list(children.order(rules.map, "a"))
    assert order.index("b") < order.index("a")

    eager = EagerPolicy()
    order = list(eager.order(rules.map, "a"))
    assert set(order) == {"a", "b"}


def test_topological_skips_missing_rule() -> None:
    from doppy_di.resolution import _topological

    rules = RuleSet()
    rules.add("a", Rule("a", lambda: 1, deps=("missing",)))
    order = _topological(rules.map, ["a"])
    assert "a" in order


def test_topological_keeps_unreachable_order() -> None:
    from doppy_di.resolution import _topological

    rules = RuleSet()
    rules.add("a", Rule("a", lambda: 1))
    rules.add("b", Rule("b", lambda: 2))
    order = _topological(rules.map, ["b", "a"])
    assert order == ["b", "a"]


def test_topological_dep_not_in_scope() -> None:
    from doppy_di.resolution import _topological

    rules = RuleSet()
    rules.add("a", Rule("a", lambda: 1, deps=("b",)))
    rules.add("b", Rule("b", lambda: 2))
    order = _topological(rules.map, ["a"])
    assert order == ["a"]


def test_parallel_policy_order_returns_topological() -> None:
    rules = RuleSet()
    rules.add("b", Rule("b", lambda: 1))
    rules.add("a", Rule("a", lambda b: b, deps=("b",)))
    order = list(ParallelPolicy().order(rules.map, "a"))
    assert order.index("b") < order.index("a")


def test_aget_with_parent_first_policy() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionParentFirstPolicy())

    assert asyncio.run(container.aget("a")) == 1


def test_aget_with_children_first_policy() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build(policy=ResolutionChildrenFirstPolicy())

    assert asyncio.run(container.aget("a")) == 1


def test_aget_with_lazy_policy() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build(policy=LazyPolicy())

    assert asyncio.run(container.aget("a")) == 1


def test_aget_with_eager_policy() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build(policy=EagerPolicy())

    assert asyncio.run(container.aget("a")) == 1


def test_parallel_policy_aget_skips_cached() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    container = builder.build(policy=ParallelPolicy())

    assert container.get("a") == 1
    assert asyncio.run(container.aget("b")) == 2


def test_parallel_policy_sync_get_with_deps() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build(policy=ParallelPolicy())

    assert container.get("b") == 2


def test_policy_depth_guard_prevents_recursion() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build(policy=ResolutionParentFirstPolicy())

    # Second call: policy already applied, root cached
    assert container.get("a") == 1
    assert container.get("a") == 1
