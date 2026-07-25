"""Tests for validation runner integration."""

from typing import Any

import pytest

from doppy_di.container import ContainerBuilder
from doppy_di.devkit.policy import UnorderedPolicy
from doppy_di.devkit.validation import ValidatingContainer, ValidationRunner


class KeySeenRule:
    """Validation rule that records resolved keys."""

    def __init__(self, seen: list[Any]) -> None:
        self.seen = seen

    def check(self, container, key, obj) -> None:  # type: ignore[no-untyped-def]
        self.seen.append(key)


def test_validation_runner_runs_rules() -> None:
    seen: list[Any] = []
    runner = ValidationRunner()
    runner.add(KeySeenRule(seen))

    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()

    container = ValidatingContainer(base, UnorderedPolicy(), runner, None)
    assert container.get("x") == 1
    assert seen == ["x"]


def test_validation_failure() -> None:
    class FailRule:
        def check(self, container, key, obj) -> None:  # type: ignore[no-untyped-def]
            raise ValueError("bad value")

    runner = ValidationRunner()
    runner.add(FailRule())

    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()

    container = ValidatingContainer(base, UnorderedPolicy(), runner, None)

    with pytest.raises(ValueError, match="bad value"):
        container.get("x")
