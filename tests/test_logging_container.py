"""Tests for logging container layer."""

from container import ContainerBuilder
from devkit.logging import LoggingContainer


def test_logging_get():
    events = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.get("x") == 42
    assert events == ["get('x')", "get('x') -> ok"]


def test_logging_has():
    events = []

    def log(msg: str) -> None:
        events.append(msg)

    builder = ContainerBuilder()
    builder.value("x", 42)
    base = builder.build()
    container = LoggingContainer(base, log)

    assert container.has("x") is True
    assert events == ["has('x')"]


def test_logging_scope():
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
