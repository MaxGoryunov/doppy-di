"""Tests for config profiles and parent/child containers."""

import json

import pytest

from doppy_di import (
    Container,
    ContainerBuilder,
)
from doppy_di.container import (
    ServiceNotFoundError,
    UnregisteredDependencyError,
    UnregisteredTypeError,
)


def test_with_profile_applies_overrides() -> None:
    builder = ContainerBuilder()
    builder.value("env", "base")
    container = builder.build()

    prod = container.with_profile("prod", {"env": "prod"})

    assert container.get("env") == "base"
    assert prod.get("env") == "prod"


def test_child_inherits_parent_rules() -> None:
    builder = ContainerBuilder()
    builder.value("db", "base-db")
    parent = builder.build()

    child = parent.child()
    child.value("extra", 1)

    assert child.get("db") == "base-db"
    assert child.get("extra") == 1


def test_child_overrides_parent() -> None:
    builder = ContainerBuilder()
    builder.value("db", "base-db")
    parent = builder.build()

    child = parent.child()
    child.value("db", "child-db")

    assert child.get("db") == "child-db"
    assert parent.get("db") == "base-db"


def test_diff_reports_changes() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.value("b", 2)
    base = builder.build()

    modded = base.child()
    modded.value("a", 10)
    modded.value("c", 3)

    diff = base.diff(modded)
    assert "a" in diff
    assert "c" in diff


def test_export_config_json() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    out = container.export_config(format="json")
    assert "a" in out


def test_with_profile_unknown_key_raises() -> None:
    builder = ContainerBuilder()
    builder.value("env", "base")
    container = builder.build()

    with pytest.raises(UnregisteredTypeError):
        container.with_profile("prod", {"missing": 1})


def test_child_preserves_profile_name() -> None:
    builder = ContainerBuilder()
    builder.value("env", "base")
    container = builder.build()

    prod = container.with_profile("prod", {"env": "prod"})
    assert prod.config.profile == "prod"


def test_child_never_mutates_parent() -> None:
    builder = ContainerBuilder()
    builder.value("db", "base-db")
    parent = builder.build()

    child = parent.child()
    child.value("extra", 1)
    child.value("db", "child-db")

    assert parent.get("db") == "base-db"
    with pytest.raises(ServiceNotFoundError):
        parent.get("extra")


def test_parent_changes_visible_to_child() -> None:
    builder = ContainerBuilder()
    builder.value("db", "base-db")
    parent = builder.build()

    child = parent.child()
    child.value("extra", 1)
    parent.value("late", "late-value")

    assert child.get("late") == "late-value"


def test_diff_reports_removed_and_changed() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda: 1)
    builder.service("b", lambda: 2)
    base = builder.build()

    other_builder = ContainerBuilder()
    other_builder.service("a", lambda: 10)
    other_builder.service("c", lambda: 3)
    other = other_builder.build()

    report = base.diff(other)
    assert "b" in report.removed
    assert "a" in report.changed
    assert "c" in report.added


def test_diff_str_formats() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    base = builder.build()

    child = base.child()
    child.value("b", 2)

    text = str(base.diff(child))
    assert "+ 'b'" in text


def test_export_config_contains_profile_and_metadata() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    prod = container.with_profile("prod", {"a": 2})

    payload = json.loads(prod.export_config())
    assert payload["profile"] == "prod"
    assert payload["rules"]["'a'"]["lifetime"] == "singleton"


def test_child_is_container() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()

    assert isinstance(container.child(), Container)


def test_composite_ruleset_merge_cache_invalidates() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    parent = builder.build()
    child = parent.child()

    before = child.config.ruleset.map
    parent.value("b", 2)
    after = child.config.ruleset.map

    assert "b" not in before
    assert "b" in after


def test_child_scope_and_singleton_isolated() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda: object(), lifetime="singleton")
    parent = builder.build()

    child = parent.child()

    assert child.get("a") is not parent.get("a")


def test_validate_on_child() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    parent = builder.build()

    child = parent.child()
    child.value("b", 1)

    assert child.validate() is None
    with pytest.raises(UnregisteredDependencyError):
        parent.validate()
