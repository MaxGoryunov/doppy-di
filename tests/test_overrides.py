"""Tests for container overrides and UnregisteredTypeError."""

import asyncio
from typing import Any, cast

import pytest

from doppy_di import UnregisteredTypeError
from doppy_di.container import ContainerBuilder
from doppy_di.providers import Scoped


def test_override_registered_key() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with container.override("x", 2):
        assert container.get("x") == 2
    assert container.get("x") == 1


def test_override_unregistered_key_raises() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(UnregisteredTypeError, match="missing"), container.override("missing", 2):
        pass


def test_override_unregistered_key_does_not_mutate_cache() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with pytest.raises(UnregisteredTypeError, match="missing"), container.override("missing", 2):
        pass

    assert "missing" not in container.single
    assert container.get("x") == 1


def test_nested_overrides_lifo() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with container.override("x", 2):
        with container.override("x", 3):
            assert container.get("x") == 3
        assert container.get("x") == 2
    assert container.get("x") == 1


def test_override_unresolved_singleton() -> None:
    builder = ContainerBuilder()
    builder.service("x", lambda: object(), lifetime="singleton")
    container = builder.build()

    with container.override("x", "mock"):
        assert container.get("x") == "mock"
    assert container.get("x") != "mock"


def test_override_restores_previous_singleton_value() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()
    assert container.get("x") == 1

    with container.override("x", 2):
        assert container.get("x") == 2
    assert container.get("x") == 1


def test_override_injectable_type_unregistered_raises() -> None:
    class Service:
        pass

    builder = ContainerBuilder()
    container = builder.build()

    with (
        pytest.raises(UnregisteredTypeError, match=str(Service)),
        container.override(Service, object()),
    ):
        pass


def test_unregistered_type_error_is_key_error() -> None:
    assert issubclass(UnregisteredTypeError, KeyError)


def test_unregistered_type_error_message() -> None:
    exc = UnregisteredTypeError("missing")
    assert str(exc) == "\"Unregistered type: 'missing'\""
    assert exc.key == "missing"


def test_override_dict_replaces_multiple() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    container = builder.build()

    with container.override({"a": 10, "b": 20}):
        assert container.get("a") == 10
        assert container.get("b") == 20

    assert container.get("a") == 1
    assert container.get("b") == 2


def test_nested_override_last_wins() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    with container.override({"a": 2}):
        with container.override({"a": 3}):
            assert container.get("a") == 3
        assert container.get("a") == 2
    assert container.get("a") == 1


def test_override_factory_value() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    with container.override({"a": lambda: 99}):
        assert container.get("a") == 99
    assert container.get("a") == 1


def test_rollback_on_exception() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    with pytest.raises(RuntimeError), container.override({"a": 2}):
        raise RuntimeError("boom")

    assert container.get("a") == 1


def test_override_singleton_with_scoped_raises() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda: object(), lifetime="singleton")
    container = builder.build()

    with (
        pytest.raises(ValueError, match="scoped dependency"),
        container.override({"a": Scoped(lambda: object(), "request")}),
    ):
        pass


def test_override_resource_with_non_resource_raises() -> None:
    def resource() -> Any:
        yield "db"

    builder = ContainerBuilder()
    builder.service("a", resource, lifetime="singleton")
    container = builder.build()

    with (
        pytest.raises(ValueError, match="non-resource"),
        container.override({"a": object()}),
    ):
        pass


def test_override_resource_with_factory_allowed() -> None:
    def resource() -> Any:
        yield "db"

    builder = ContainerBuilder()
    builder.service("a", resource, lifetime="singleton")
    container = builder.build()

    with container.override({"a": lambda: "fake"}):
        assert container.get("a") == "fake"


def test_override_dict_validation_is_atomic() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    with (
        pytest.raises(UnregisteredTypeError, match="missing"),
        container.override({"a": 10, "missing": 20}),
    ):
        pass

    assert container._override_layers == []
    assert container.get("a") == 1


def test_override_layer_empty_when_unused() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    assert container._override_layers == []
    assert container.get("a") == 1


def test_override_kwargs_supported() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    container = builder.build()

    with container.override("a", 10, b=20):
        assert container.get("a") == 10
        assert container.get("b") == 20


def test_override_async_get() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    async def main() -> int:
        with container.override({"a": 42}):
            return cast(int, await container.aget("a"))

    assert asyncio.run(main()) == 42


def test_override_async_restores() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    async def main() -> None:
        with container.override({"a": 42}):
            assert await container.aget("a") == 42

    asyncio.run(main())
    assert container.get("a") == 1


def test_override_async_factory() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    async def main() -> int:
        with container.override({"a": lambda: 99}):
            return cast(int, await container.aget("a"))

    assert asyncio.run(main()) == 99
