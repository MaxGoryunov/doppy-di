"""Static typing checks verified by mypy --strict.

This module is not executed at runtime. It exists so mypy can verify that
modern typing features (ParamSpec, Self, TypeGuard) are usable with the
public API.
"""

from __future__ import annotations

from typing import Callable, ParamSpec, TypeVar

from doppy_di import ContainerBuilder, Factory, Provider, is_injectable


class Database:
    def __init__(self, host: str = "localhost") -> None:
        self.host = host


def make_database() -> Database:
    return Database()


def test_paramspec_factory() -> None:
    P = ParamSpec("P")  # noqa: N806
    T = TypeVar("T")

    def provider(factory: Callable[P, T]) -> Callable[P, T]:
        return factory

    builder = ContainerBuilder()
    builder.service(Database, make=provider(make_database))
    container = builder.build()
    db = container.get(Database)
    assert isinstance(db, Database)


def test_factory_protocol() -> None:
    P = ParamSpec("P")  # noqa: N806
    T = TypeVar("T")

    def make(host: str) -> Database:
        return Database(host)

    def accept(factory: Factory[P, T]) -> None:
        assert callable(factory)

    accept(make)


def test_provider_alias() -> None:
    P = ParamSpec("P")  # noqa: N806
    T = TypeVar("T")

    def accept(prov: Provider[P, T]) -> None:
        assert callable(prov)

    accept(make_database)


def test_self_chaining() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1).service("y", lambda: 2).alias("z", "x")
    container = builder.build()
    assert container.get("z") == 1


def test_typeguard() -> None:
    from doppy_di import injectable

    @injectable
    class Service:
        pass

    if is_injectable(Service):
        assert isinstance(Service, type)
