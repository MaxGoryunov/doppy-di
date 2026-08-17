"""Tests for compile/plan mode (issue #32)."""

from __future__ import annotations

import pytest

from doppy_di import (
    CompilePolicy,
    ContainerBuilder,
    DependencyCycleError,
    ExecutionPlan,
    MissingDependencyError,
)


def test_compile_basic_resolution() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()
    plan = container.compile()
    assert isinstance(plan, ExecutionPlan)
    assert plan.get("b") == 2


def test_compile_topological_order() -> None:
    builder = ContainerBuilder()
    builder.value("c", 3)
    builder.service("b", lambda c: c + 1, deps=["c"])
    builder.service("a", lambda b: b + 1, deps=["b"])
    container = builder.build()
    plan = container.compile()
    order = list(plan.order)
    assert order.index("'c'") < order.index("'b'")
    assert order.index("'b'") < order.index("'a'")


def test_compile_missing_dependency_raises() -> None:
    builder = ContainerBuilder()
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()
    with pytest.raises(MissingDependencyError):
        container.compile()


def test_compile_cycle_raises() -> None:
    builder = ContainerBuilder(check_cycles_on_register=False)
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()
    with pytest.raises(DependencyCycleError):
        container.compile()


def test_compile_strict_policy_blocks_override() -> None:
    builder = ContainerBuilder(compile_policy=CompilePolicy.STRICT)
    builder.value("x", 1)
    container = builder.build()
    plan = container.compile()
    with pytest.raises(RuntimeError), container.override("x", 2):
        pass
    assert plan.get("x") == 1


def test_compile_allow_override_policy_allows_override() -> None:
    builder = ContainerBuilder(compile_policy=CompilePolicy.ALLOW_OVERRIDE)
    builder.value("x", 1)
    container = builder.build()
    plan = container.compile()
    with container.override("x", 2):
        assert plan.get("x") == 2
    assert plan.get("x") == 1


def test_plan_uses_container_caches() -> None:
    builder = ContainerBuilder()
    builder.service("s", lambda: object(), lifetime="singleton")
    container = builder.build()

    plan = container.compile()
    assert plan.get("s") is container.get("s")


def test_compile_singleton_cached_in_plan() -> None:
    counter = {"n": 0}

    def factory() -> int:
        counter["n"] += 1
        return counter["n"]

    builder = ContainerBuilder()
    builder.service("x", factory, lifetime="singleton")
    container = builder.build()
    plan = container.compile()
    assert plan.get("x") == 1
    assert plan.get("x") == 1
    assert counter["n"] == 1


def test_compile_serialize_roundtrip() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", "hello")
    container = builder.build()
    plan = container.compile()
    data = plan.serialize()
    restored = ExecutionPlan.deserialize(data)
    assert restored.order == plan.order
    assert {k: list(v) for k, v in restored.edges.items()} == {
        k: list(v) for k, v in plan.edges.items()
    }
    assert restored.get("a") == 1
    assert restored.get("b") == "hello"
