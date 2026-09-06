"""Child containers, module installation and isolation semantics."""

import asyncio
import logging
from typing import Any, Dict, List

import pytest

from doppy_di import (
    Container,
    ContainerBuilder,
    DuplicateKeyPolicy,
    DuplicateRegistrationError,
    Rule,
    ServiceNotFoundError,
)
from doppy_di.container import CompositeRuleSet
from doppy_di.module import ModuleBinder


class DatabaseModule:
    @staticmethod
    def configure(binder: ModuleBinder) -> None:
        binder.value("db", "parent-db")


class AppModule:
    def configure(self, binder: ModuleBinder) -> None:
        binder.service("app", lambda db: f"app:{db}", deps=["db"])


def test_child_sees_parent_rules() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()
    assert child.get("db") == "parent-db"


def test_child_sees_rules_added_after_creation() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    parent.config.ruleset.add("late", Rule("late", lambda: 1))
    assert child.get("late") == 1


def test_child_shadows_parent_key() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()
    child.config.ruleset.add("db", Rule("db", lambda: "child-db"))
    assert child.get("db") == "child-db"
    assert parent.get("db") == "parent-db"


def test_child_rules_do_not_leak_into_parent() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    child.config.ruleset.add("extra", Rule("extra", lambda: 1))
    assert child.get("extra") == 1
    with pytest.raises(ServiceNotFoundError):
        parent.get("extra")


def test_child_ruleset_is_composite() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    assert isinstance(child.config.ruleset, CompositeRuleSet)


def test_singleton_identity_shared_when_parent_resolved_first() -> None:
    parent = ContainerBuilder().service("db", lambda: object(), lifetime="singleton").build()
    child = parent.create_child()
    assert parent.get("db") is child.get("db")


def test_singleton_identity_shared_when_child_resolves_first() -> None:
    parent = ContainerBuilder().service("db", lambda: object(), lifetime="singleton").build()
    child = parent.create_child()
    obj = child.get("db")
    assert parent.get("db") is obj


def test_singleton_identity_via_child_dependency_chain() -> None:
    parent = ContainerBuilder().service("db", lambda: object(), lifetime="singleton").build()
    child = parent.create_child()
    child.config.ruleset.add("svc", Rule("svc", lambda db: db, deps=("db",)))
    assert child.get("svc") is parent.get("db")


def test_unshared_singletons_are_distinct() -> None:
    parent = ContainerBuilder().service("db", lambda: object(), lifetime="singleton").build()
    child = parent.create_child(share_singletons=False)
    assert child.get("db") is not parent.get("db")


def test_child_override_does_not_affect_parent() -> None:
    parent = ContainerBuilder().value("cfg", "base").build()
    child = parent.create_child()
    with child.override("cfg", "overridden"):
        assert child.get("cfg") == "overridden"
        assert parent.get("cfg") == "base"


def test_builder_install_module() -> None:
    builder = ContainerBuilder()
    builder.install(DatabaseModule(), AppModule())
    container = builder.build()
    assert container.get("app") == "app:parent-db"


def test_builder_install_module_class() -> None:
    class SimpleModule:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("solo", 1)

    builder = ContainerBuilder()
    builder.install(SimpleModule)  # type: ignore[arg-type]  # classes accepted at runtime
    assert builder.build().get("solo") == 1


def test_container_install_module_writes_local_layer() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()
    child.install(AppModule())
    assert child.get("app") == "app:parent-db"
    assert not parent.has("app")


def test_install_function_module() -> None:
    def module(binder: ModuleBinder) -> None:
        binder.value("x", 1)

    container = ContainerBuilder().install(module).build()
    assert container.get("x") == 1


def test_install_duplicate_fail_policy() -> None:
    builder = ContainerBuilder()

    class Dup:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("dup", 1)

    class Dup2:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("dup", 2)

    with pytest.raises(DuplicateRegistrationError):
        builder.install(Dup(), Dup2(), duplicate_policy=DuplicateKeyPolicy.FAIL)


def test_module_may_shadow_parent_key() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()

    class Shadow:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("db", "shadow-db")

    child.install(Shadow())
    assert child.get("db") == "shadow-db"


def test_child_validate_parent_dep_via_compile() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()
    child.config.ruleset.add("svc", Rule("svc", lambda db: db, deps=("db",)))
    plan = child.compile()
    assert plan.get("svc") == "parent-db"


def test_child_missing_dep_still_errors() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    child.config.ruleset.add("svc", Rule("svc", lambda db: db, deps=("missing",)))
    with pytest.raises(Exception, match="missing"):
        child.compile()


def test_child_scopes_isolated() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    assert child.scopes == {}
    assert child.single is not parent.single


def test_child_async_resolution() -> None:
    parent = ContainerBuilder().value("db", "parent-db").build()
    child = parent.create_child()
    assert asyncio.run(child.aget("db")) == "parent-db"


def test_child_traced_resolution() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    events: List[Any] = []
    child.set_tracer(lambda *args: events.append(args))
    child.config.ruleset.add("x", Rule("x", lambda: 1))
    child.get("x")
    assert len(events) == 1


def test_module_binder_alias() -> None:
    builder = ContainerBuilder()
    binder = ModuleBinder(builder.rules)
    binder.value("x", 1)
    binder.alias("y", "x")
    assert builder.build().get("y") == 1


def test_install_instance_module() -> None:
    builder = ContainerBuilder()

    class InstanceModule:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("inst", "yes")

    builder.install(InstanceModule())
    assert builder.build().get("inst") == "yes"


def test_install_warn_policy_logs(caplog: Any) -> None:
    builder = ContainerBuilder()
    binder = ModuleBinder(builder.rules, DuplicateKeyPolicy.WARN)
    binder.value("dup", 1)
    with caplog.at_level(logging.WARNING, logger="doppy_di.module"):
        binder.value("dup", 2)
    assert caplog.records


def test_child_local_singleton_stays_in_child_cache() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child()
    child.config.ruleset.add("local", Rule("local", lambda: object(), lifetime="singleton"))
    a = child.get("local")
    assert child.get("local") is a
    assert "local" in child.single
    assert "local" not in parent.single


def test_child_singleton_cache_get_and_getitem() -> None:
    from doppy_di.container import _ChildSingletonCache

    parent_cache: Dict[Any, Any] = {"x": "px"}
    cache = _ChildSingletonCache(parent_cache, lambda key: key == "local")
    assert cache.get("missing") is None
    assert cache.get("x") == "px"
    cache["local"] = "l"
    assert cache.get("local") == "l"
    assert cache["x"] == "px"
    assert cache["local"] == "l"
    with pytest.raises(KeyError):
        cache["missing"]


def test_container_install_duplicate_fail_on_plain_container() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    class D1:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("dup", 1)

    class D2:
        def configure(self, binder: ModuleBinder) -> None:
            binder.value("dup", 2)

    child = container.create_child()
    with pytest.raises(DuplicateRegistrationError):
        child.install(D1(), D2(), duplicate_policy=DuplicateKeyPolicy.FAIL)


def test_builder_install_function_and_alias() -> None:
    builder = ContainerBuilder()

    def first(binder: ModuleBinder) -> None:
        binder.value("one", 1)
        binder.alias("one_alias", "one")

    builder.install(first)
    c = builder.build()
    assert c.get("one_alias") == 1


def test_unshared_child_service_singletons() -> None:
    parent = ContainerBuilder().build()
    child = parent.create_child(share_singletons=False)
    child.config.ruleset.add("svc", Rule("svc", lambda: object(), lifetime="singleton"))
    assert child.get("svc") is child.get("svc")


def test_child_returns_container_type() -> None:
    parent = ContainerBuilder().build()
    assert isinstance(parent.create_child(), Container)
