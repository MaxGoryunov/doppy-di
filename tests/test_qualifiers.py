"""Tests for named dependencies (qualifiers)."""

from typing import Annotated, Any, Callable

import pytest

from doppy_di import (
    ContainerBuilder,
    Qualifier,
    UnregisteredDependencyError,
    inject,
    injectable,
)


class Database:
    """Simple test model with a name."""

    def __init__(self, name: str) -> None:
        self.name = name


def _db(name: str) -> Callable[[], Database]:
    def make() -> Database:
        return Database(name)

    return make


def test_register_and_get_with_qualifier() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    builder.service(Database, _db("write"), qualifier="write")
    container = builder.build()

    db_read = container.get(Database, qualifier="read")
    db_write = container.get(Database, qualifier="write")

    assert db_read.name == "read"
    assert db_write.name == "write"


def test_default_resolution_unaffected() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("default"))
    container = builder.build()

    db = container.get(Database)  # qualifier=None
    assert db.name == "default"


def test_missing_qualifier_raises() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("default"))
    container = builder.build()

    with pytest.raises(UnregisteredDependencyError):
        container.get(Database, qualifier="missing")


def test_qualifier_and_type_are_distinct_keys() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    builder.service(Database, _db("write"), qualifier="write")
    builder.service(Database, _db("default"))
    container = builder.build()

    assert container.get(Database).name == "default"
    assert container.get(Database, qualifier="read").name == "read"
    assert container.get(Database, qualifier="write").name == "write"


def test_aget_with_qualifier() -> None:
    import asyncio

    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    container = builder.build()

    async def main() -> Any:
        return (await container.aget(Database, qualifier="read")).name

    assert asyncio.run(main()) == "read"


def test_has_and_get_or_none_with_qualifier() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    container = builder.build()

    assert container.has(Database, qualifier="read") is True
    assert container.has(Database, qualifier="missing") is False
    assert container.get_or_none(Database, qualifier="missing") is None


@injectable(qualifier="read")
class ReadService:
    """Injectable class with a qualifier."""


def test_injectable_qualifier_auto_wiring() -> None:
    container = ContainerBuilder().build()
    container.scan(__name__)
    obj = container.get(ReadService, qualifier="read")
    assert isinstance(obj, ReadService)


@injectable
class QualifiedDep:
    def __init__(self, db: Annotated[Database, Qualifier("read")]) -> None:
        self.db = db


def test_annotated_qualifier_in_auto_wired_dep() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    container = builder.build()

    obj = container.get(QualifiedDep)
    assert obj.db.name == "read"


def test_annotated_qualifier_in_inject() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    container = builder.build()

    @inject(container=container)
    def resolve(db: Annotated[Database, Qualifier("read")]) -> Any:
        return str(db.name)

    assert resolve() == "read"


def test_override_with_qualifier_tuple_key() -> None:
    builder = ContainerBuilder()
    builder.service(Database, _db("read"), qualifier="read")
    container = builder.build()

    with container.override((Database, "read"), Database("replaced")):
        assert container.get(Database, qualifier="read").name == "replaced"
    assert container.get(Database, qualifier="read").name == "read"
