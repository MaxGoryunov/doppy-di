"""Tests for container overrides and UnregisteredTypeError."""

import pytest

from doppy_di import UnregisteredTypeError
from doppy_di.container import ContainerBuilder


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

    with pytest.raises(UnregisteredTypeError), container.override("missing", 2):
        pass


def test_override_unregistered_key_does_not_mutate_cache() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with pytest.raises(UnregisteredTypeError), container.override("missing", 2):
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

    with pytest.raises(UnregisteredTypeError), container.override(Service, object()):
        pass


def test_unregistered_type_error_is_key_error() -> None:
    assert issubclass(UnregisteredTypeError, KeyError)


def test_unregistered_type_error_message() -> None:
    exc = UnregisteredTypeError("missing")
    assert str(exc) == "\"Unregistered type: 'missing'\""
    assert exc.key == "missing"
