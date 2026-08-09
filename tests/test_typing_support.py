"""Runtime tests for modern typing feature support."""

from typing import Callable, ParamSpec, TypeAlias, TypedDict, TypeVar

from doppy_di import ContainerBuilder, Factory, Provider, injectable, is_injectable


class Database:
    def __init__(self, host: str = "localhost") -> None:
        self.host = host


DatabaseService: TypeAlias = Database


def test_typealias_usable_as_key() -> None:
    builder = ContainerBuilder()
    builder.service(DatabaseService, make=lambda: Database())
    container = builder.build()

    assert isinstance(container.get(DatabaseService), Database)


def test_typeddict_resolvable() -> None:
    class DBConfig(TypedDict):
        host: str
        port: int

    builder = ContainerBuilder()
    builder.service(DBConfig, make=lambda: {"host": "localhost", "port": 5432})
    container = builder.build()

    config = container.get(DBConfig)
    assert config["host"] == "localhost"
    assert config["port"] == 5432


def test_paramspec_factory_typing() -> None:
    P = ParamSpec("P")  # noqa: N806
    T = TypeVar("T")

    def make_database() -> Database:
        return Database()

    def provider(factory: Callable[P, T]) -> Callable[P, T]:
        return factory

    builder = ContainerBuilder()
    builder.service(Database, make=provider(make_database))
    container = builder.build()

    assert isinstance(container.get(Database), Database)


def test_factory_protocol_accepts_callable() -> None:
    def make(host: str) -> Database:
        return Database(host)

    assert isinstance(make, Factory)


def test_provider_alias_is_callable() -> None:
    P = ParamSpec("P")  # noqa: N806
    T = TypeVar("T")

    def make() -> Database:
        return Database()

    def accept(prov: Provider[P, T]) -> None:
        assert callable(prov)

    accept(make)


def test_typeguard_detects_injectable() -> None:
    @injectable
    class Service:
        pass

    assert is_injectable(Service) is True
    assert is_injectable(int) is False


def test_self_return_type_chaining() -> None:
    builder = ContainerBuilder()
    result = builder.value("x", 1).service("y", lambda: 2).alias("z", "x")
    assert result is builder
    container = builder.build()
    assert container.get("z") == 1
    assert container.get("y") == 2


def test_self_return_type_service() -> None:
    builder = ContainerBuilder()
    assert builder.service("x", lambda: 1) is builder


def test_self_return_type_value() -> None:
    builder = ContainerBuilder()
    assert builder.value("x", 1) is builder


def test_self_return_type_alias() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    assert builder.alias("y", "x") is builder


def test_is_injectable_negative() -> None:
    assert is_injectable(object) is False
