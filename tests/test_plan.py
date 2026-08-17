"""Tests for compile/plan mode (issue #32)."""

from __future__ import annotations

import asyncio
import json

import pytest

from doppy_di import (
    CompilePolicy,
    ContainerBuilder,
    DependencyCycleError,
    ExecutionPlan,
    MissingDependencyError,
    Rule,
    RuleSet,
    ServiceNotFoundError,
)
from doppy_di.plan import _topological_order


class _Service:
    pass


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


def test_plan_get_falls_back_for_key_outside_snapshot() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    plan = container.compile()
    container.value("late", 5)
    assert plan.get("late") == 5


def test_plan_resolve_ordered_skips_missing_key_mapping() -> None:
    builder = ContainerBuilder()
    builder.value("b", 2)
    container = builder.build()
    plan = ExecutionPlan(
        container=container,
        order=("'a'", "'b'"),
        edges={},
        rules={},
        keys={"'b'": "b"},
        singletons={},
    )
    assert plan.get("b") == 2


def test_plan_resolve_ordered_skips_unresolvable_dep() -> None:
    builder = ContainerBuilder()
    builder.value("b", 2)
    container = builder.build()
    plan = ExecutionPlan(
        container=container,
        order=("'a'", "'b'"),
        edges={},
        rules={},
        keys={"'a'": "a", "'b'": "b"},
        singletons={},
    )
    assert plan.get("b") == 2


def test_plan_resolve_ordered_skips_cached_dep() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()
    assert container.get("a") == 1
    plan = container.compile()
    assert plan.get("b") == 2


def test_plan_get_missing_key_after_deserialize_raises() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    with pytest.raises(ServiceNotFoundError):
        restored.get("missing")


def test_plan_aget_live_container() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    plan = builder.build().compile()

    async def main() -> int:
        return int(await plan.aget("a"))

    assert asyncio.run(main()) == 1


def test_plan_aget_after_deserialize_raises() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())

    async def main() -> None:
        with pytest.raises(ServiceNotFoundError):
            await restored.aget("a")

    asyncio.run(main())


def test_compile_child_container_snapshots_parent_rules() -> None:
    builder = ContainerBuilder()
    builder.value("parent", 1)
    parent = builder.build()
    child = parent.child()
    child.value("x", 2)
    plan = child.compile()
    assert plan.get("parent") == 1
    assert plan.get("x") == 2


def test_plan_serialize_unsupported_format_raises() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    plan = builder.build().compile()
    with pytest.raises(ValueError, match="Unsupported serialize format"):
        plan.serialize("pickle")


def test_plan_serialize_transient_rule_skips_snapshot() -> None:
    builder = ContainerBuilder()
    builder.service("t", lambda: object())
    plan = builder.build().compile()
    data = plan.serialize()
    assert "'t'" in data


def test_plan_serialize_resolved_singleton_skips_resolution() -> None:
    builder = ContainerBuilder()
    builder.value("s", 1)
    container = builder.build()
    assert container.get("s") == 1
    plan = container.compile()
    data = plan.serialize()
    assert "'s'" in data


def test_plan_serialize_singleton_without_key_mapping() -> None:
    plan = ExecutionPlan(
        container=None,
        order=("'x'",),
        edges={},
        rules={"'x'": {"lifetime": "singleton"}},
        keys={},
        singletons={},
    )
    assert plan.serialize()


def test_plan_serialize_unresolvable_singleton_logs() -> None:
    builder = ContainerBuilder()
    builder.value("b", 2)
    container = builder.build()
    plan = ExecutionPlan(
        container=container,
        order=("'a'",),
        edges={},
        rules={"'a'": {"lifetime": "singleton"}},
        keys={"'a'": "a"},
        singletons={},
    )
    assert plan.serialize()


def test_plan_reserialize_deserialized_plan() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert restored.serialize()


def test_plan_serialize_singleton_without_key_mapping_live() -> None:
    builder = ContainerBuilder()
    builder.value("b", 2)
    container = builder.build()
    plan = ExecutionPlan(
        container=container,
        order=("'a'",),
        edges={},
        rules={"'a'": {"lifetime": "singleton"}},
        keys={},
        singletons={},
    )
    assert plan.serialize()


def test_plan_serialize_type_key_roundtrip() -> None:
    builder = ContainerBuilder()
    builder.value(_Service, _Service())
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert isinstance(restored.get(_Service), str)


def test_plan_serialize_qualifier_key_roundtrip() -> None:
    builder = ContainerBuilder()
    builder.service(
        "db",
        lambda: "read-db",
        qualifier="read",
        lifetime="singleton",
    )
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert restored.get("db", qualifier="read") == "read-db"


def test_plan_serialize_custom_key_roundtrip() -> None:
    class _CustomKey:
        def __init__(self, name: str) -> None:
            self.name = name

        def __hash__(self) -> int:
            return hash(self.name)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, _CustomKey) and other.name == self.name

        def __repr__(self) -> str:
            return f"_CustomKey({self.name!r})"

    key = _CustomKey("k")
    builder = ContainerBuilder()
    builder.value(key, 1)
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert restored.get(key) == 1


def test_plan_serialize_object_value_uses_repr() -> None:
    builder = ContainerBuilder()
    builder.value("obj", object())
    plan = builder.build().compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert isinstance(restored.get("obj"), str)


def test_plan_deserialize_raw_singleton_value() -> None:
    data = json.dumps(
        {
            "order": ["'x'"],
            "edges": {},
            "rules": {},
            "keys": {"'x'": {"__str__": "x"}},
            "singletons": {"'x'": 42},
            "policy": "allow_override",
        }
    )
    restored = ExecutionPlan.deserialize(data)
    assert restored.get("x") == 42


def test_topological_order_detects_cycle() -> None:
    ruleset = RuleSet(defer_cycle_check=True)
    ruleset.add("a", Rule("a", lambda: 1, deps=("b",)))
    ruleset.add("b", Rule("b", lambda: 1, deps=("a",)))
    scope = dict(ruleset.map)
    with pytest.raises(DependencyCycleError):
        _topological_order(ruleset, scope)


def test_topological_order_multi_dep_levels() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    builder.service("d", lambda a, b: a + b, deps=["a", "b"])
    plan = builder.build().compile()
    order = list(plan.order)
    assert order.index("'a'") < order.index("'d'")
    assert order.index("'b'") < order.index("'d'")
