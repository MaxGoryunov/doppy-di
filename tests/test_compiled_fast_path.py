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
    plan = container.compile(allow_post_compile_overrides=True)

    fake = ApiClient(Settings())
    with container.override(ApiClient, fake):
        assert plan.get(ApiClient) is fake
    assert plan.get(ApiClient) is not fake


def test_plan_frozen_blocks_post_compile_override() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    fake = ApiClient(Settings())
    with pytest.raises(RuntimeError), container.override(ApiClient, fake):
        pass
    assert plan.get(ApiClient) is not fake


def test_plan_frozen_singleton_identity_with_container() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)

    a = plan.get(ApiClient)
    b = plan.get(ApiClient)
    assert a is b
    assert a is container.get(ApiClient)


def test_plan_frozen_nested_singleton_uses_resolve_fast() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.rules.add(
        ("s", "v"),
        Rule(
            ("s", "v"),
            lambda v: {"s": v},
            lifetime="singleton",
            deps=("v",),
            nested=True,
        ),
    )
    container = builder.build()
    plan = container.compile(allow_post_compile_overrides=False)

    # Nested singletons are excluded from resolvers, so get() must fall
    # through to _resolve_fast and read the frozen constant.
    assert plan.get(("s", "v")) == {"s": 1}
    assert plan.get(("s", "v")) is container.get(("s", "v"))


def test_plan_frozen_recursive_singleton_chain_identity() -> None:
    class S1:
        pass

    class S2:
        def __init__(self, s1: S1) -> None:
            self.s1 = s1

    class S3:
        def __init__(self, s2: S2) -> None:
            self.s2 = s2

    builder = ContainerBuilder()
    builder.service(S1, S1, lifetime="singleton")
    builder.service(S2, S2, lifetime="singleton", deps=[S1])
    builder.service(S3, S3, lifetime="singleton", deps=[S2])
    container = builder.build()
    plan = container.compile(allow_post_compile_overrides=False)

    assert set(plan._frozen) == {S1, S2, S3}
    assert plan.get(S1) is container.get(S1)
    assert plan.get(S2) is container.get(S2)
    assert plan.get(S3) is container.get(S3)
    assert plan.get(S1) is plan.get(S1)
    assert plan.get(S2).s1 is plan.get(S1)
    assert plan.get(S3).s2 is plan.get(S2)


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


def test_plan_closure_built_for_benchmark_graph() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    assert set(plan.resolvers) == {
        Settings,
        UserRepository,
        EmailSender,
        AuditLog,
        RegisterUser,
        ApiClient,
    }


def test_plan_closure_used_on_hot_path() -> None:
    calls: List[Any] = []
    container = _build_benchmark_container()
    plan = container.compile()
    original = plan.resolvers[RegisterUser]

    def spy() -> Any:
        calls.append(RegisterUser)
        return original()

    plan.resolvers[RegisterUser] = spy
    obj = plan.get(RegisterUser)
    assert calls == [RegisterUser]
    assert isinstance(obj, RegisterUser)


def test_plan_closure_bypassed_when_override_active() -> None:
    calls: List[Any] = []
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=True)
    original = plan.resolvers[RegisterUser]

    def spy() -> Any:
        calls.append(1)
        return original()

    plan.resolvers[RegisterUser] = spy
    with container.override(ApiClient, ApiClient(Settings())):
        obj = plan.get(RegisterUser)
    assert calls == []
    assert isinstance(obj, RegisterUser)


def test_plan_closure_bypassed_when_tracer_active() -> None:
    calls: List[Any] = []
    events: List[Any] = []
    container = _build_benchmark_container()
    plan = container.compile()
    original = plan.resolvers[RegisterUser]

    def spy() -> Any:
        calls.append(1)
        return original()

    plan.resolvers[RegisterUser] = spy
    container.set_tracer(lambda key, duration, cache_hit, scope: events.append(key))
    plan.get(RegisterUser)
    assert calls == []
    assert RegisterUser in events


def test_plan_closure_arity_zero() -> None:
    builder = ContainerBuilder()
    builder.service("zero", lambda: 42)
    plan = builder.build().compile()
    assert "zero" in plan.resolvers
    assert plan.get("zero") == 42


def test_plan_closure_arity_one() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("one", lambda a: a + 1, deps=["a"])
    plan = builder.build().compile()
    assert "one" in plan.resolvers
    assert plan.get("one") == 2


def test_plan_closure_arity_two() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    builder.service("two", lambda a, b: a + b, deps=["a", "b"])
    plan = builder.build().compile()
    assert "two" in plan.resolvers
    assert plan.get("two") == 3


def test_plan_closure_generic_arity() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    builder.value("c", 3)
    builder.service("many", lambda *deps: sum(deps), deps=["a", "b", "c"])
    plan = builder.build().compile()
    assert "many" in plan.resolvers
    assert plan.get("many") == 6


def test_plan_closure_excludes_async() -> None:
    async def make_async() -> int:
        return 1

    builder = ContainerBuilder()
    builder.service("a", make_async)
    plan = builder.build().compile()
    assert "a" not in plan.resolvers
    assert asyncio.run(plan.aget("a")) == 1


def test_plan_closure_excludes_yield() -> None:
    def make_yield() -> Any:
        yield 1

    builder = ContainerBuilder()
    builder.service("y", make_yield)
    plan = builder.build().compile()
    assert "y" not in plan.resolvers


def test_plan_closure_excludes_nested() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("child", 1)
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
    plan = builder.build().compile()
    assert ("parent", "child") not in plan.resolvers


def test_plan_deserialize_resolvers_empty() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert restored.resolvers == {}


# --- Issue #40: flattened transient subgraphs --------------------------------


def test_plan_flat_resolver_kind_literal_for_benchmark_graph() -> None:
    container = _build_benchmark_container()
    plan = container.compile()

    assert plan.resolver_kinds[RegisterUser] == "flat"
    assert plan.resolver_kinds[UserRepository] == "flat"
    obj = plan.get(RegisterUser)
    assert isinstance(obj, RegisterUser)
    assert isinstance(obj.repo.client, ApiClient)
    assert isinstance(obj.repo.client.settings, Settings)


def test_plan_flat_deep_transient_chain_uses_generic_tier() -> None:
    builder = ContainerBuilder()
    builder.value("s", 7)
    builder.service("t1", lambda s: ["t1", s], deps=["s"])
    builder.service("t2", lambda t1: ["t2", t1], deps=["t1"])
    builder.service("t3", lambda t2: ["t3", t2], deps=["t2"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["t3"] == "generic"
    a = plan.get("t3")
    b = plan.get("t3")
    assert a is not b
    assert a[0] == "t3"
    assert a[1][0] == "t2"
    assert a[1][1][0] == "t1"
    assert a[1][1][1] == 7


@pytest.mark.parametrize(
    "recipe",
    [
        "p",
        "l1",
        "pp",
        "pl1",
        "l1p",
        "l1l1",
        "ppp",
        "ppl1",
        "pl1p",
        "pl1l1",
        "l1pp",
        "l1pl1",
        "l1l1p",
        "l1l1l1",
    ],
)
def test_plan_flat_literal_slot_patterns(recipe: str) -> None:
    builder = ContainerBuilder()
    builder.value("v", 10)
    builder.service("api", lambda v: ["api", v], lifetime="singleton", deps=["v"])

    # Parse recipe into slots: 'p' = prelude value ref, 'l1' = leaf call.
    deps: List[Any] = []
    kinds: List[str] = []
    pos = 0
    while pos < len(recipe):
        if recipe[pos] == "p":
            deps.append("v")
            kinds.append("p")
            pos += 1
        else:
            deps.append("api")
            kinds.append("l1")
            pos += 2

    def make_root(*args: Any) -> List[Any]:
        return ["root", *args]

    builder.service("root", make_root, deps=deps)
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "flat"
    obj = plan.get("root")
    assert obj[0] == "root"
    for i, kind in enumerate(kinds):
        if kind == "p":
            assert obj[i + 1] == 10
        else:
            assert obj[i + 1] == ["api", 10]


def test_plan_flat_literal_prelude_sizes() -> None:
    builder = ContainerBuilder()
    builder.value("v1", 1)
    builder.value("v2", 2)
    builder.value("v3", 3)
    builder.service("s1", lambda v1: ["s1", v1], lifetime="singleton", deps=["v1"])
    builder.service("s2", lambda v2: ["s2", v2], lifetime="singleton", deps=["v2"])
    builder.service("root3", lambda a, b, c: [a, b, c], deps=["s1", "v3", "s2"])
    builder.service("root4", lambda a, b, c: [a, b, c], deps=["s1", "s2", "v1"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["root3"] == "flat"
    assert plan.resolver_kinds["root4"] == "flat"
    assert plan.get("root3") == [["s1", 1], 3, ["s2", 2]]
    assert plan.get("root4") == [["s1", 1], ["s2", 2], 1]


def test_plan_flat_prelude_over_unroll_limit_is_generic() -> None:
    builder = ContainerBuilder()
    names: List[Any] = [f"v{i}" for i in range(6)]
    for name in names:
        builder.value(name, name)
    builder.service("root", lambda *xs: list(xs), deps=names)
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "generic"
    assert plan.get("root") == names


def test_plan_flat_generic_wide_root_arity() -> None:
    builder = ContainerBuilder()
    for name in ("a", "b", "c", "d", "e"):
        builder.value(name, name)
    builder.service("root", lambda *xs: list(xs), deps=["a", "b", "c", "d", "e"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "generic"
    assert plan.get("root") == ["a", "b", "c", "d", "e"]


def test_plan_flat_generic_leaf_multi_dep() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    builder.service("t", lambda a, b: ["t", a, b], deps=["a", "b"])
    builder.service("root", lambda t: ["root", t], deps=["t"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "generic"
    assert plan.get("root") == ["root", ["t", 1, 2]]


def test_plan_flat_singleton_root_shares_cache() -> None:
    builder = ContainerBuilder()
    builder.value("v", 3)
    builder.service("t", lambda v: ["t", v], deps=["v"])
    builder.service("root", lambda t: ["root", t], lifetime="singleton", deps=["t"])
    container = builder.build()
    plan = container.compile()

    assert plan.resolver_kinds["root"] == "flat"
    a = plan.get("root")
    b = plan.get("root")
    assert a is b
    assert container.get("root") is a


def test_plan_flat_duplicate_dep_slots() -> None:
    builder = ContainerBuilder()
    builder.value("v", 5)
    builder.service("root", lambda x, y: x + y, deps=["v", "v"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "flat"
    assert plan.get("root") == 10


def test_plan_flat_fallback_when_yield_in_subtree() -> None:
    def make_yield() -> Any:
        yield 1

    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("y", make_yield)
    builder.service("root", lambda y, v: [y, v], deps=["y", "v"])
    plan = builder.build().compile()

    assert "root" not in plan.resolver_kinds
    import types

    assert isinstance(plan.get("root"), types.GeneratorType)


def test_plan_flat_fallback_when_async_in_subtree() -> None:
    async def make_async() -> int:
        return 2

    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("a", make_async)
    builder.service("root", lambda a, v: [a, v], deps=["a", "v"])
    plan = builder.build().compile()

    assert "root" not in plan.resolver_kinds
    assert asyncio.run(plan.aget("root")) == [2, 1]


def test_plan_flat_nested_alias_excluded() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("child", 1)
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
    plan = builder.build().compile()

    assert ("parent", "child") not in plan.resolver_kinds


# --- Issue #40 coverage: transient-leaf literal patterns ---------------------


@pytest.mark.parametrize(
    "recipe",
    ["l", "pl", "lp", "ll", "ppl", "plp", "pll", "lpp", "lpl", "llp", "lll"],
)
def test_plan_flat_literal_transient_leaf_patterns(recipe: str) -> None:
    builder = ContainerBuilder()
    builder.value("v", 10)
    builder.service("t", lambda v: ["t", v], deps=["v"])

    deps: List[Any] = []
    kinds: List[str] = []
    for ch in recipe:
        if ch == "p":
            deps.append("v")
            kinds.append("p")
        else:
            deps.append("t")
            kinds.append("l1")

    def make_root(*args: Any) -> List[Any]:
        return ["root", *args]

    builder.service("root", make_root, deps=deps)
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "flat"
    obj = plan.get("root")
    assert obj[0] == "root"
    for i, kind in enumerate(kinds):
        expected = 10 if kind == "p" else ["t", 10]
        assert obj[i + 1] == expected


def test_plan_flat_generic_without_singletons() -> None:
    builder = ContainerBuilder()
    builder.service("t0", lambda: ["t0"])
    builder.service("t1", lambda t0: ["t1", t0], deps=["t0"])
    builder.service("t2", lambda t1: ["t2", t1], deps=["t1"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["t2"] == "generic"
    a = plan.get("t2")
    b = plan.get("t2")
    assert a is not b
    assert a[1][1] == ["t0"]


def test_plan_flat_generic_expr_arity_three_and_wide() -> None:
    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("ta", lambda v: ["ta", v], deps=["v"])
    builder.service("t1", lambda v: ["t1", v], deps=["v"])
    builder.service("tb", lambda v: ["tb", v], deps=["v"])
    builder.service(
        "mid3",
        lambda ta, t1, tb: ["m3", ta, t1, tb],
        deps=["ta", "t1", "tb"],
    )
    builder.service(
        "wide",
        lambda a, b, c, d: ["w", a, b, c, d],
        deps=["ta", "tb", "v", "ta"],
    )
    plan = builder.build().compile()

    assert plan.resolver_kinds["mid3"] == "flat"
    assert plan.resolver_kinds["wide"] == "generic"
    m = plan.get("mid3")
    assert m[0] == "m3"
    assert m[1] == ["ta", 1]
    assert m[2] == ["t1", 1]
    assert m[3] == ["tb", 1]
    w = plan.get("wide")
    assert w[:2] == ["w", ["ta", 1]]
    assert w[4] == ["ta", 1]


def test_plan_flat_generic_root_arity_two_and_three() -> None:
    # r2/r3 children are leaf transients over a prelude value -> literal tier.
    builder = ContainerBuilder()
    builder.value("v", 5)
    builder.service("t", lambda v: ["t", v], deps=["v"])
    builder.service("u", lambda v: ["u", v], deps=["v"])
    builder.service("r2", lambda t, u: ["r2", t, u], deps=["t", "u"])
    builder.service("r3", lambda t, u, w: ["r3", t, u, w], deps=["t", "u", "t"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["r2"] == "flat"
    assert plan.resolver_kinds["r3"] == "flat"
    a = plan.get("r2")
    assert a[0] == "r2"
    assert a[1] == ["t", 5]
    assert a[2] == ["u", 5]
    b = plan.get("r3")
    assert b[0] == "r3"
    assert b[1] == ["t", 5]
    assert b[2] == ["u", 5]
    assert b[3] == ["t", 5]


def test_plan_composed_cold_build_multi_dep_singletons() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    builder.value("c", 3)
    builder.value("d", 4)
    builder.value("e", 5)
    builder.service(
        "s2",
        lambda a, b: ["s2", a, b],
        lifetime="singleton",
        deps=["a", "b"],
    )
    builder.service(
        "s3",
        lambda a, b, c: ["s3", a, b, c],
        lifetime="singleton",
        deps=["a", "b", "c"],
    )
    builder.service(
        "s5",
        lambda a, b, c, d, e: ["s5", a, b, c, d, e],
        lifetime="singleton",
        deps=["a", "b", "c", "d", "e"],
    )
    plan = builder.build().compile()

    assert plan.get("s2") == ["s2", 1, 2]
    assert plan.get("s3") == ["s3", 1, 2, 3]
    assert plan.get("s5") == ["s5", 1, 2, 3, 4, 5]
    assert plan.get("s2") is plan.get("s2")


def test_plan_flat_resolver_direct_ineligible_root() -> None:
    from doppy_di import plan as plan_mod

    def make_yield() -> Any:
        yield 1

    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("y", make_yield)
    container = builder.build()
    p = container.compile()
    nodes = p.nodes
    yield_idx = next(i for i, s in enumerate(nodes) if s.yield_provider)
    makers_none: List[Any] = [None] * len(nodes)

    # Root itself ineligible.
    assert plan_mod._build_flat_resolver(yield_idx, nodes, makers_none, container) is None

    # Mid-subtree ineligible: transient root depending on the yield node.
    builder2 = ContainerBuilder()
    builder2.value("v", 1)
    builder2.service("y", make_yield)
    builder2.service("root", lambda y, v: [y, v], deps=["y", "v"])
    c2 = builder2.build()
    p2 = c2.compile()
    n2 = p2.nodes
    r_idx = next(i for i, s in enumerate(n2) if s.key == "root")
    assert plan_mod._build_flat_resolver(r_idx, n2, makers_none[: len(n2)], c2) is None

    # Prelude wrapper missing: singleton maker never built.
    builder3 = ContainerBuilder()
    builder3.value("v", 1)
    builder3.service("s", lambda v: ["s", v], lifetime="singleton", deps=["v"])
    builder3.service("root", lambda s: ["r", s], deps=["s"])
    c3 = builder3.build()
    p3 = c3.compile()
    n3 = p3.nodes
    r3_idx = next(i for i, s in enumerate(n3) if s.key == "root")
    assert plan_mod._build_flat_resolver(r3_idx, n3, makers_none[: len(n3)], c3) is None


def test_plan_flat_caps_fall_back(monkeypatch: Any) -> None:
    import doppy_di.plan as plan_mod

    monkeypatch.setattr(plan_mod, "_MAX_FLAT_DEPTH", 0)
    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("t", lambda v: ["t", v], deps=["v"])
    builder.service("root", lambda t: ["r", t], deps=["t"])
    plan = builder.build().compile()

    assert plan.resolver_kinds["root"] == "composed"
    assert plan.get("root") == ["r", ["t", 1]]

    monkeypatch.setattr(plan_mod, "_MAX_FLAT_NODES", 1)
    plan2 = builder.build().compile()
    assert plan2.resolver_kinds["root"] == "composed"


def test_plan_compile_factory_signature_unreadable() -> None:
    builder = ContainerBuilder()
    builder.service("mp", map)
    plan = builder.build().compile()
    # Signature inspection failed at compile time; the key still resolves.
    assert "mp" in plan.resolvers


def test_plan_deserialize_skips_unknown_order_entries() -> None:
    import json as _json

    builder = ContainerBuilder()
    builder.value("s", 42)
    plan = builder.build().compile()
    payload = _json.loads(plan.serialize())
    payload["order"].append("'ghost'")
    restored = ExecutionPlan.deserialize(_json.dumps(payload))
    assert restored.get("s") == 42


def test_plan_fast_walk_singleton_cache_hit_under_override() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=True)
    assert plan.get(RegisterUser) is not None
    with container.override(Settings, Settings()):
        obj = plan.get(RegisterUser)
    assert isinstance(obj, RegisterUser)


def test_plan_frozen_override_raises_after_compile() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    assert plan.get(RegisterUser) is not None
    with pytest.raises(RuntimeError), container.override(Settings, Settings()):
        pass


def test_plan_frozen_prefetches_singletons() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    assert set(plan._frozen) == {Settings, ApiClient}
    assert isinstance(plan._frozen[Settings], Settings)
    assert isinstance(plan._frozen[ApiClient], ApiClient)
    assert plan._frozen[ApiClient].settings is plan._frozen[Settings]


def test_plan_frozen_lockless_resolver_kind() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    assert plan.resolver_kinds[RegisterUser] == "flat"
    assert plan.resolver_kinds[ApiClient] == "frozen"
    assert plan.resolver_kinds[Settings] == "frozen"


def test_plan_frozen_constant_inlined() -> None:
    builder = ContainerBuilder()
    builder.value("v", 42)
    builder.service("root", lambda v: v + 1, deps=["v"])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    assert plan.resolver_kinds["v"] == "frozen"
    assert plan.get("root") == 43


def test_plan_frozen_compile_with_active_override_raises() -> None:
    container = _build_benchmark_container()
    with container.override(ApiClient, ApiClient(Settings())), pytest.raises(RuntimeError):
        container.compile(allow_post_compile_overrides=False)


def test_plan_frozen_serialize_roundtrip() -> None:
    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    restored = ExecutionPlan.deserialize(plan.serialize())
    assert set(restored._frozen) == {Settings, ApiClient}
    assert restored.frozen is True
    assert restored.get(ApiClient) is not None


def test_plan_frozen_constant_override_ignored() -> None:
    builder = ContainerBuilder()
    builder.value("v", 1)
    container = builder.build()
    plan = container.compile(allow_post_compile_overrides=False)
    with pytest.raises(RuntimeError), container.override("v", 2):
        pass
    assert plan.get("v") == 1


def test_plan_fast_walk_nested_alias_direct() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("child", 1)
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
    plan = builder.build().compile()
    assert plan.get(("parent", "child")) == 1


# --- Issue #43: exec-free acceleration ----------------------------------------


def test_plan_bind_returns_bound_resolver() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    bound = plan.bind(RegisterUser)
    assert callable(bound)
    assert bound() is not None
    assert isinstance(bound(), RegisterUser)


def test_plan_bind_exposes_kind() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    bound = plan.bind(RegisterUser)
    assert bound.kind == plan.resolver_kinds[RegisterUser]


def test_plan_bind_unknown_key_raises() -> None:
    container = _build_benchmark_container()
    plan = container.compile()
    with pytest.raises(ServiceNotFoundError):
        plan.bind("missing")


def test_plan_bind_qualifier_key() -> None:
    builder = ContainerBuilder()
    builder.service("k", lambda: 1, qualifier="q", lifetime="singleton")
    plan = builder.build().compile(allow_post_compile_overrides=False)
    bound = plan.bind("k", "q")
    assert bound() == 1
    assert bound.kind == "frozen"


def test_plan_frozen_resolver_uses_constants() -> None:
    from doppy_di import plan as plan_mod  # noqa: F401

    container = _build_benchmark_container()
    plan = container.compile(allow_post_compile_overrides=False)
    assert plan.resolver_kinds[RegisterUser] == "flat"
    # No prelude fetch: resolver is a direct closure over constants.
    resolver = plan.resolvers[RegisterUser]
    bytecode = getattr(resolver, "__code__", None)
    assert bytecode is not None
    # The frozen singleton constants are captured, not looked up dynamically.
    obj = resolver()
    assert isinstance(obj, RegisterUser)
    assert obj.repo.client.settings is plan._frozen[Settings]


def test_plan_frozen_transient_chain_identity() -> None:
    class S1:
        pass

    class S2:
        def __init__(self, s1: S1) -> None:
            self.s1 = s1

    class S3:
        def __init__(self, s2: S2) -> None:
            self.s2 = s2

    builder = ContainerBuilder()
    builder.service(S1, S1, lifetime="singleton")
    builder.service(S2, S2, lifetime="singleton", deps=[S1])
    builder.service(S3, S3, lifetime="singleton", deps=[S2])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    obj = plan.get(S3)
    assert obj.s2.s1 is plan._frozen[S1]
    assert obj.s2.s1 is plan._frozen[S1]
    assert plan.get(S3) is obj


def test_plan_frozen_inline_transients_no_prelude() -> None:
    builder = ContainerBuilder()
    builder.value("v", 7)
    builder.service("t", lambda v: ["t", v], deps=["v"])
    builder.service("root", lambda t: ["r", t], deps=["t"])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    assert plan.get("root") == ["r", ["t", 7]]


def test_plan_frozen_shared_singleton_constant_once() -> None:
    builder = ContainerBuilder()
    builder.value("v", 9)
    builder.service("shared", lambda v: ["s", v], lifetime="singleton", deps=["v"])
    builder.service("root", lambda a, b: ["r", a, b], deps=["shared", "shared"])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    obj = plan.get("root")
    assert obj[1] is obj[2]
    assert obj[1] is plan._frozen["shared"]


def test_plan_frozen_shared_transient_falls_back() -> None:
    # Shared transient has two parents -> frozen resolver falls back to
    # composed resolver (recomputes). Fallback preserves value semantics.
    builder = ContainerBuilder()
    builder.value("v", 1)
    builder.service("shared", lambda v: ["s", v], deps=["v"])
    builder.service("root", lambda a, b: ["r", a, b], deps=["shared", "shared"])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    obj = plan.get("root")
    assert obj[0] == "r"
    assert obj[1] == ["s", 1]
    assert obj[2] == ["s", 1]


def test_plan_frozen_arity_zero_and_one() -> None:
    builder = ContainerBuilder()
    builder.value("c", 42)
    builder.service("z", lambda: 43)
    builder.service("one", lambda c: c + 1, deps=["c"])
    plan = builder.build().compile(allow_post_compile_overrides=False)
    assert plan.get("z") == 43
    assert plan.get("one") == 43


def test_plan_frozen_rejects_nested() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("child", 1)
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
    plan = builder.build().compile(allow_post_compile_overrides=False)
    # Nested aliases use container.get fallback path.
    assert plan.get(("parent", "child")) == 1
