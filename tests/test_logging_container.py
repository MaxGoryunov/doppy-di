"""Tests for logging container layer."""

import pytest

from doppy_di.container import ContainerBuilder
from doppy_di.devkit.logging import LoggingContainer


def test_logging_get() -> None:
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.get("x") == 42
    assert events == ["get('x')", "get('x') -> ok"]


def test_logging_has() -> None:
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.has("x") is True
    assert events == ["has('x')"]


def test_logging_scope() -> None:
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    scope = container.scope("req")
    assert scope.get("x") == 42
    assert events[0] == "scope('req')"


def test_logging_container_base_exception_not_caught() -> None:
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)

    def raise_system_exit() -> object:
        raise SystemExit(1)

    builder = ContainerBuilder()
    builder.service("x", raise_system_exit)
    base = builder.build()

    container = LoggingContainer(base, log)

    with pytest.raises(SystemExit):  # NOSONAR python:S8714 error-path assertion
        container.get("x")

    assert any("error" in event for event in events)
