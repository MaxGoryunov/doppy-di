"""Tests for cycle detection in RuleSet."""

import pytest

from container import ContainerBuilder, CycleError


def test_no_cycle():
    builder = ContainerBuilder()
    builder.service("a", lambda: 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()

    assert container.get("b") == 2


def test_direct_cycle():
    builder = ContainerBuilder()

    builder.service("a", lambda b: b, deps=["b"])
    with pytest.raises(CycleError):
        builder.service("b", lambda a: a, deps=["a"])


def test_indirect_cycle():
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda c: c, deps=["c"])
    with pytest.raises(CycleError):
        builder.service("c", lambda a: a, deps=["a"])


def test_cycle_error_path():
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    with pytest.raises(CycleError) as exc:
        builder.service("b", lambda a: a, deps=["a"])
    assert "a" in repr(exc.value.path) or "b" in repr(exc.value.path)
