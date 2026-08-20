"""Tests for the declarative provider facade."""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from doppy_di import Container, Scope
from doppy_di.providers import (
    Alias,
    Coroutine,
    DictOf,
    Factory,
    ListOf,
    Provider,
    Resource,
    Scoped,
    Selector,
    Singleton,
    Value,
)


class UserRepository:
    def __init__(self, db: str = "db") -> None:
        self.db = db


class UserService:
    def __init__(self, repo: UserRepository | None = None) -> None:
        self.repo = repo or UserRepository()


def test_factory_provider() -> None:
    services = Container()
    services.repo = Factory(UserRepository, db=services.db)

    assert isinstance(services.get(UserRepository), UserRepository)


def test_factory_provider_does_not_cache() -> None:
    services = Container()
    services.repo = Factory(lambda: {})

    assert services.get("repo") is not services.get("repo")


def test_singleton_provider_caches() -> None:
    services = Container()
    services.service = Singleton(  # type: ignore[method-assign, assignment]
        UserService
    )

    assert services.get(UserService) is services.get(UserService)


def test_value_provider() -> None:
    services = Container()
    services.config = Value({"debug": True})  # type: ignore[assignment]

    assert services.get("config") == {"debug": True}


def test_alias_provider() -> None:
    services = Container()
    services.repo = Factory(UserRepository)
    services.alias = Alias("repo")

    assert services.get("alias") is services.get("repo")


def test_list_of_provider() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.all = ListOf(services.a, services.b)

    assert services.get("all") == [1, 2]


def test_dict_of_provider() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.mapping = DictOf(a=services.a, b=services.b)

    assert services.get("mapping") == {"a": 1, "b": 2}


def test_selector_provider() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.pick = Selector(
        {"a": services.a, "b": services.b},
        selector_fn=lambda ctx: "b",
    )

    assert services.get("pick") == 2


def test_scoped_provider_caches_within_scope() -> None:
    services = Container()
    services.req = Scoped(list, Scope.REQUEST)

    with services.scope("req") as s:
        assert s.get("req") is s.get("req")


def test_scoped_provider_fresh_per_scope() -> None:
    services = Container()
    services.req = Scoped(list, Scope.REQUEST)

    with services.scope("req") as s1:
        first = s1.get("req")
    with services.scope("req") as s2:
        second = s2.get("req")

    assert first is not second


def test_scoped_provider_accepts_string_scope() -> None:
    services = Container()
    services.req = Scoped(list, "request")

    with services.scope("request") as s:
        assert s.get("req") is s.get("req")


class FakeScope:
    value = "fake"


def test_scoped_provider_scope_enum_value() -> None:
    services = Container()
    services.req = Scoped(list, FakeScope())  # type: ignore[arg-type]

    with services.scope("fake") as s:
        assert s.get("req") is s.get("req")


def test_scoped_provider_callable_no_type_alias() -> None:
    services = Container()
    services.req = Scoped(lambda: [], "request")

    with services.scope("request") as s:
        assert s.get("req") is s.get("req")


class BareScope:
    pass


def test_scoped_provider_scope_name_fallback() -> None:
    services = Container()
    services.req = Scoped(list, BareScope())  # type: ignore[arg-type]

    with services.scope("BareScope") as s:
        assert s.get("req") is s.get("req")


def test_singleton_provider_callable() -> None:
    services = Container()
    services.counter = Singleton(lambda: [])

    assert services.get("counter") is services.get("counter")


def test_resource_provider_finalizes_on_scope_exit() -> None:
    closed: List[str] = []

    def create_db() -> Any:
        yield "db"
        closed.append("db")

    services = Container()
    services.db = Resource(create_db, Scope.APP)

    with services.scope("app") as s:
        assert s.get("db") == "db"
    assert closed == ["db"]


def test_coroutine_provider() -> None:
    async def make() -> int:
        return 42

    services = Container()
    services.answer = Coroutine(make)

    assert asyncio.run(services.aget("answer")) == 42


def test_factory_with_dependencies() -> None:
    services = Container()
    services.db = Value("postgres")
    services.repo = Factory(UserRepository, db=services.db)

    assert services.get("repo").db == "postgres"


def test_singleton_with_dependencies() -> None:
    services = Container()
    services.repo = Factory(UserRepository)
    services.service = Singleton(  # type: ignore[method-assign, assignment]
        UserService, repo=services.repo
    )

    assert services.get("service").repo is services.get("repo")


def test_type_key_registration() -> None:
    services = Container()
    services.repo = Factory(UserRepository)

    assert services.get("repo") is services.get(UserRepository)


def test_provider_retrieval_via_attribute() -> None:
    services = Container()
    services.repo = Factory(UserRepository)

    assert isinstance(services.repo, Factory)


def test_unbound_provider_dependency_raises() -> None:
    services = Container()
    unbound = Factory(UserRepository)

    with pytest.raises(ValueError, match="not bound"):
        services.repo = Factory(UserService, repo=unbound)


def test_provider_base_to_rules_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        Provider().to_rules("x")


def test_zero_overhead_when_providers_unused() -> None:
    services = Container()
    assert services.config.ruleset.keys() == ()


def test_scope_constants() -> None:
    assert Scope.APP == "app"
    assert Scope.REQUEST == "request"
    assert Scope.SESSION == "session"


def test_container_no_arg_constructor() -> None:
    services = Container()
    assert services.config.ruleset.keys() == ()


def test_selector_with_context() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.pick = Selector(
        {"a": services.a, "b": services.b},
        selector_fn=lambda ctx: "a",
    )

    assert services.get("pick") == 1


def test_list_of_with_raw_keys() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.all = ListOf("a", "b")

    assert services.get("all") == [1, 2]


def test_dict_of_with_raw_keys() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.mapping = DictOf(a="a", b="b")

    assert services.get("mapping") == {"a": 1, "b": 2}


def test_alias_to_provider() -> None:
    services = Container()
    services.repo = Factory(UserRepository)
    services.alias = Alias(services.repo)

    assert services.get("alias") is services.get("repo")


def test_selector_unknown_key_raises() -> None:
    services = Container()
    services.a = Value(1)
    services.b = Value(2)
    services.pick = Selector(
        {"a": services.a, "b": services.b},
        selector_fn=lambda ctx: "missing",
    )

    with pytest.raises(ValueError, match="not in list"):
        services.get("pick")
