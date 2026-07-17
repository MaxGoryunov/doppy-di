"""Tests for logging container layer."""

from container import ContainerBuilder
from devkit.logging import LoggingContainer


def test_logging_get() -> None:
    events = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.get("x") == 42
    assert events == ["get('x')", "get('x') -> ok"]


def test_logging_has() -> None:
    events = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.has("x") is True
    assert events == ["has('x')"]


def test_logging_scope() -> None:
    events = []

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
    events = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.service("x", lambda: (_ for _ in ()).throw(SystemExit(1)))
    base = builder.build()

    container = LoggingContainer(base, log)

    import pytest

    with pytest.raises(SystemExit):
        container.get("x")

    assert any("error" in e for e in events)
