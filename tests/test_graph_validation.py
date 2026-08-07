"""Tests for static graph validation (Container.validate())."""

import pytest

from doppy_di import (
    CyclicDependencyError,
    InvalidFactoryError,
    UnregisteredDependencyError,
    ValidationError,
)
from doppy_di.container import (
    Container,
    ContainerBuilder,
    ContainerConfig,
    Rule,
    RuleSet,
)


def test_validate_strict_raises_on_missing_dependency() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(ValidationError):
        container.validate(strict=True)


def test_validate_non_strict_returns_errors() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    errors = container.validate(strict=False)
    assert errors is not None
    assert len(errors) == 1
    assert isinstance(errors[0], UnregisteredDependencyError)


def _cyclic_container() -> Container:
    rules = RuleSet(
        {
            "a": Rule("a", lambda b: b, deps=("b",)),
            "b": Rule("b", lambda a: a, deps=("a",)),
        },
        {"a": ("b",), "b": ("a",)},
    )
    return Container(ContainerConfig(rules))


def test_validate_detects_cycle() -> None:
    container = _cyclic_container()

    with pytest.raises(ValidationError):
        container.validate(strict=True)


def test_validate_ok_on_valid_graph() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    assert container.validate(strict=True) is None
    assert container.validate(strict=False) == []


def test_validate_non_strict_cycle_error_type() -> None:
    container = _cyclic_container()

    errors = container.validate(strict=False)
    assert errors is not None
    assert any(isinstance(e, CyclicDependencyError) for e in errors)


def test_validate_invalid_factory_too_few_deps() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.service("a", lambda x, y: x, deps=["x"])
    container = builder.build()

    with pytest.raises(InvalidFactoryError):
        container.validate(strict=True)


def test_validate_invalid_factory_too_many_deps() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.value("y", 2)
    builder.service("a", lambda x: x, deps=["x", "y"])
    container = builder.build()

    with pytest.raises(InvalidFactoryError):
        container.validate(strict=True)


def test_validate_invalid_factory_non_strict() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.service("a", lambda x, y: x, deps=["x"])
    container = builder.build()

    errors = container.validate(strict=False)
    assert errors is not None
    assert any(isinstance(e, InvalidFactoryError) for e in errors)


def test_validate_varargs_factory_ok() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.value("y", 2)
    builder.service("a", lambda *args: args, deps=["x", "y"])
    container = builder.build()

    assert container.validate(strict=True) is None


def test_validate_error_attributes() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    errors = container.validate(strict=False)
    assert errors is not None
    err = errors[0]
    assert isinstance(err, UnregisteredDependencyError)
    assert err.key == "a"
    assert err.dependency == "b"


def test_validate_cycle_error_attributes() -> None:
    container = _cyclic_container()

    errors = container.validate(strict=False)
    assert errors is not None
    cycle = next(e for e in errors if isinstance(e, CyclicDependencyError))
    assert "a" in cycle.path
    assert "b" in cycle.path


def test_validate_invalid_factory_attributes() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.service("a", lambda x, y: x, deps=["x"])
    container = builder.build()

    errors = container.validate(strict=False)
    assert errors is not None
    err = next(e for e in errors if isinstance(e, InvalidFactoryError))
    assert err.key == "a"
    assert "args" in err.reason


def test_validate_strict_raises_first_error() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("c", lambda d: d, deps=["d"])
    container = builder.build()

    with pytest.raises(ValidationError):
        container.validate(strict=True)


def test_validate_non_strict_collects_all_errors() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("c", lambda d: d, deps=["d"])
    container = builder.build()

    errors = container.validate(strict=False)
    assert errors is not None
    assert len(errors) == 2
    assert all(isinstance(e, UnregisteredDependencyError) for e in errors)


def test_validate_empty_container() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    assert container.validate(strict=True) is None
    assert container.validate(strict=False) == []
