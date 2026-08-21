"""Tests for compiled plan fast-path resolution (issue #37)."""

from __future__ import annotations

import asyncio
import inspect
import typing
from typing import Any, List

import pytest

from doppy_di import (
    CompilePolicy,
    ContainerBuilder,
    ExecutionPlan,
    ServiceNotFoundError,
)


class Settings:
    pass


class ApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class UserRepository:
    def __init__(self, client: ApiClient) -> None:
        self.client = client


class EmailSender:
    def __init__(self, client: ApiClient) -> None:
        self.client = client


class AuditLog:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class RegisterUser:
    def __init__(
        self,
        repo: UserRepository,
        email: EmailSender,
        audit: AuditLog,
    ) -> None:
        self.repo = repo
        self.email = email
        self.audit = audit


def _build_benchmark_container() -> Any:
    builder = ContainerBuilder()
    builder.value(Settings, Settings())
    builder.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    builder.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    builder.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    builder.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    builder.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )
    return builder.build()


def test_plan_resolves_without_container_delegation() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    class _BoomMap(dict[object, object]):
        def __getitem__(self, key: Any) -> Any:
            raise AssertionError("plan.get() must not delegate to container.get()")

    original_map: dict[object, object] = container.config.ruleset.map
    container.config.ruleset.map = _BoomMap(original_map)  # type: ignore[assignment]
    try:
        obj = plan.get(RegisterUser)
    finally:
        container.config.ruleset.map = original_map  # type: ignore[assignment]

    assert isinstance(obj, RegisterUser)
    assert isinstance(obj.repo, UserRepository)
    assert isinstance(obj.email, EmailSender)
    assert isinstance(obj.audit, AuditLog)
    assert isinstance(obj.repo.client, ApiClient)
    assert isinstance(obj.repo.client.settings, Settings)


def test_plan_singleton_identity_with_container() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    a = plan.get(ApiClient)
    b = plan.get(ApiClient)
    assert a is b
    assert a is container.get(ApiClient)


def test_plan_transient_freshness() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    a = plan.get(UserRepository)
    b = plan.get(UserRepository)
    assert a is not b


def test_plan_shared_singleton_across_transients() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    a = plan.get(RegisterUser)
    b = plan.get(RegisterUser)
    assert a is not b
    assert a.repo.client is b.repo.client
    assert a.repo.client.settings is b.repo.client.settings


def test_plan_allow_override_visible() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    fake = ApiClient(Settings())
    with container.override(ApiClient, fake):
        assert plan.get(ApiClient) is fake
    assert plan.get(ApiClient) is not fake


def test_plan_strict_blocks_override() -> None:
    builder = ContainerBuilder(compile_policy=CompilePolicy.STRICT)
    builder.value("x", 1)
    container = builder.build()
    plan = container.compile()

    with pytest.raises(RuntimeError), container.override("x", 2):
        pass
    assert plan.get("x") == 1


def test_plan_late_key_falls_back_to_container() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    container.value("late", 5)
    assert plan.get("late") == 5


def test_plan_unknown_key_after_deserialize_raises() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    with pytest.raises(ServiceNotFoundError):
        restored.get("missing")


def test_plan_aget_delegates() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    async def main() -> RegisterUser:
        obj = await plan.aget(RegisterUser)
        assert isinstance(obj, RegisterUser)
        return obj

    obj = asyncio.run(main())
    assert isinstance(obj, RegisterUser)


def test_plan_no_reflection_after_compile() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    original_signature = inspect.signature
    original_get_type_hints = typing.get_type_hints

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("reflection called on hot path")

    inspect.signature = boom  # type: ignore[assignment]
    typing.get_type_hints = boom  # type: ignore[assignment]
    try:
        obj = plan.get(RegisterUser)
    finally:
        inspect.signature = original_signature  # type: ignore[assignment]
        typing.get_type_hints = original_get_type_hints  # type: ignore[assignment]

    assert isinstance(obj, RegisterUser)


def test_plan_tracer_emits_events() -> None:
    events: List[Any] = []
    container = _build_benchmark_container()
    container.set_tracer(lambda key, duration, cache_hit, scope: events.append(key))
    plan = container.compile()

    plan.get(RegisterUser)

    assert RegisterUser in events
    assert ApiClient in events


def test_plan_nested_alias_cached() -> None:
    from doppy_di import Rule

    class Parent:
        def __init__(self, child: Any) -> None:
            self.child = child

    builder = ContainerBuilder()
    builder.value("child", object())
    builder.service("parent", lambda child: Parent(child), deps=["child"])
    builder.rules.add(
        ("parent", "child"),
        Rule(
            ("parent", "child"),
            lambda child: child,
            lifetime="transient",
            deps=("child",),
            nested=True,
        ),
    )
    container = builder.build()
    plan = container.compile()

    parent = plan.get("parent")
    assert container.get(("parent", "child")) is parent.child


def test_plan_container_none_walk_make_none_raises() -> None:
    from doppy_di.plan import _NodeSpec

    spec = _NodeSpec(
        key="a",
        make=None,
        deps_idx=(),
        lifetime="transient",
        yield_provider=False,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    plan = ExecutionPlan(
        container=None,
        order=("a",),
        edges={},
        rules={},
        keys={"a": "a"},
        singletons={},
        node_index={"a": 0},
        nodes=(spec,),
    )
    with pytest.raises(ServiceNotFoundError):
        plan.get("a")


def test_plan_make_none_delegates_to_container() -> None:
    from doppy_di.plan import _NodeSpec

    container = _build_benchmark_container()
    container.value("alone", 5)
    spec = _NodeSpec(
        key="alone",
        make=None,
        deps_idx=(),
        lifetime="transient",
        yield_provider=False,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    plan = ExecutionPlan(
        container=container,
        order=("alone",),
        edges={},
        rules={},
        keys={"alone": "alone"},
        singletons={},
        node_index={"alone": 0},
        nodes=(spec,),
    )
    assert plan.get("alone") == 5


def test_plan_yield_provider_delegates_to_container() -> None:
    from doppy_di.plan import _NodeSpec

    container = _build_benchmark_container()
    container.value("y", 7)
    spec = _NodeSpec(
        key="y",
        make=lambda: 1,
        deps_idx=(),
        lifetime="transient",
        yield_provider=True,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    plan = ExecutionPlan(
        container=container,
        order=("y",),
        edges={},
        rules={},
        keys={"y": "y"},
        singletons={},
        node_index={"y": 0},
        nodes=(spec,),
    )
    assert plan.get("y") == 7


def test_plan_container_none_unknown_key_raises() -> None:
    from doppy_di.plan import _NodeSpec

    spec = _NodeSpec(
        key="a",
        make=lambda: 1,
        deps_idx=(),
        lifetime="transient",
        yield_provider=False,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    plan = ExecutionPlan(
        container=None,
        order=("a",),
        edges={},
        rules={},
        keys={"a": "a"},
        singletons={},
        node_index={"a": 0},
        nodes=(spec,),
    )
    with pytest.raises(ServiceNotFoundError):
        plan.get("missing")


def test_plan_container_none_walk_with_deps() -> None:
    from doppy_di.plan import _NodeSpec

    dep = _NodeSpec(
        key="dep",
        make=lambda: 3,
        deps_idx=(),
        lifetime="transient",
        yield_provider=False,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    root = _NodeSpec(
        key="root",
        make=lambda d: d + 1,
        deps_idx=(0,),
        lifetime="transient",
        yield_provider=False,
        async_yield_provider=False,
        is_async=False,
        nested=False,
    )
    plan = ExecutionPlan(
        container=None,
        order=("dep", "root"),
        edges={},
        rules={},
        keys={"dep": "dep", "root": "root"},
        singletons={},
        node_index={"dep": 0, "root": 1},
        nodes=(dep, root),
    )
    assert plan.get("root") == 4


def test_plan_empty_nodes_singleton_hit() -> None:
    plan = ExecutionPlan(
        container=None,
        order=(),
        edges={},
        rules={},
        keys={},
        singletons={"'x'": 42},
    )
    assert plan.get("x") == 42


def test_plan_empty_nodes_missing_raises() -> None:
    plan = ExecutionPlan(
        container=None,
        order=(),
        edges={},
        rules={},
        keys={},
        singletons={},
    )
    with pytest.raises(ServiceNotFoundError):
        plan.get("x")


def test_plan_deserialized_singleton_hit() -> None:
    builder = ContainerBuilder()
    builder.value("s", 42)
    container = builder.build()
    plan = container.compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert restored.get("s") == 42


def test_plan_singleton_none_value() -> None:
    builder = ContainerBuilder()
    builder.value("x", None)
    container = builder.build()
    plan = container.compile()

    assert plan.get("x") is None
    assert plan.get("x") is None


def test_plan_thread_safety_singleton() -> None:
    import threading

    call_count = 0
    lock = threading.Lock()

    def make_obj() -> dict[str, int]:
        nonlocal call_count
        with lock:
            call_count += 1
            return {"id": call_count}

    builder = ContainerBuilder()
    builder.service("x", make_obj, lifetime="singleton")
    container = builder.build()
    plan = container.compile()

    results: List[Any] = []
    errors: List[Exception] = []
    barrier = threading.Barrier(10)

    def get_x() -> None:
        barrier.wait()
        try:
            results.append(plan.get("x"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=get_x) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert call_count == 1
    ids = {r["id"] for r in results}
    assert len(ids) == 1


def test_plan_compile_rejects_cycle() -> None:
    from doppy_di import DependencyCycleError

    builder = ContainerBuilder(check_cycles_on_register=False)
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()

    with pytest.raises(DependencyCycleError):
        container.compile()


def test_plan_compile_rejects_missing_dep() -> None:
    from doppy_di import MissingDependencyError

    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(MissingDependencyError):
        container.compile()


def test_plan_compile_rejects_factory_arity_too_few_deps() -> None:
    from doppy_di import InvalidFactoryError

    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a, b: a + b, deps=["a"])
    container = builder.build()

    with pytest.raises(InvalidFactoryError):
        container.compile()


def test_plan_compile_rejects_factory_arity_too_many_deps() -> None:
    from doppy_di import InvalidFactoryError

    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("c", 2)
    builder.service("b", lambda a: a, deps=["a", "c"])
    container = builder.build()

    with pytest.raises(InvalidFactoryError):
        container.compile()


def test_plan_deserialize_rebuilds_node_index_and_nodes() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()
    plan = container.compile()
    restored = ExecutionPlan.deserialize(plan.serialize())

    assert "a" in restored.node_index
    assert "b" in restored.node_index
    assert len(restored.nodes) == 2

    # Factories are not serialized → standalone resolution must raise,
    # not silently delegate or crash.
    with pytest.raises(ServiceNotFoundError):
        restored.get("b")
