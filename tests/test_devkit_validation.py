"""Tests for validation runner integration."""

import pytest

from container import ContainerBuilder
from devkit.policy import UnorderedPolicy
from devkit.validation import ValidatingContainer, ValidationRunner


class KeySeenRule:
    """Validation rule that records resolved keys."""

    def __init__(self, seen):
        self.seen = seen

    def check(self, container, key, obj):
        self.seen.append(key)


def test_validation_runner_runs_rules():
    seen = []
    runner = ValidationRunner()
    runner.add(KeySeenRule(seen))

    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()

    container = ValidatingContainer(base, UnorderedPolicy(), runner, None)
    assert container.get("x") == 1
    assert seen == ["x"]


def test_validation_failure():
    class FailRule:
        def check(self, container, key, obj):
            raise ValueError("bad value")

    runner = ValidationRunner()
    runner.add(FailRule())

    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()

    container = ValidatingContainer(base, UnorderedPolicy(), runner, None)

    with pytest.raises(ValueError, match="bad value"):
        container.get("x")
