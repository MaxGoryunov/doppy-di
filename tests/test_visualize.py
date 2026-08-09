"""Tests for Container.visualize() dependency graph rendering."""

import pytest

from doppy_di.container import (
    Container,
    ContainerBuilder,
    ContainerConfig,
    Rule,
    RuleSet,
)
from doppy_di.devkit.visualize import build_model


def test_visualize_mermaid() -> None:
    builder = ContainerBuilder()
    builder.value("db", object())
    builder.service("service", lambda db: db, deps=["db"])
    container = builder.build()

    out = container.visualize(format="mermaid")
    assert "graph TD" in out
    assert "service --> db" in out


def test_visualize_mermaid_singleton_and_scope_shape() -> None:
    builder = ContainerBuilder()
    builder.value("db", object())
    builder.service(
        "service",
        lambda db: db,
        deps=["db"],
        lifetime="singleton",
        scope="request",
    )
    container = builder.build()

    out = container.visualize(format="mermaid")
    assert "([service])singleton" in out
    assert "[db]singleton" in out


def test_visualize_graphviz() -> None:
    builder = ContainerBuilder()
    builder.value("db", object())
    builder.service("service", lambda db: db, deps=["db"])
    container = builder.build()

    out = container.visualize(format="graphviz")
    assert "digraph G" in out
    assert '"service" -> "db";' in out


def test_visualize_graphviz_lifetime_and_scope() -> None:
    builder = ContainerBuilder()
    builder.service(
        "service",
        lambda: object(),
        lifetime="singleton",
        scope="app",
    )
    container = builder.build()

    out = container.visualize(format="graphviz")
    assert "shape=stadium, color=singleton" in out


def test_visualize_json() -> None:
    builder = ContainerBuilder()
    builder.value("db", object())
    builder.service("service", lambda db: db, deps=["db"])
    container = builder.build()

    out = container.visualize(format="json")
    assert out["service"]["deps"] == ["db"]
    assert out["service"]["lifetime"] == "transient"
    assert out["db"]["lifetime"] == "singleton"
    assert out["service"]["scope"] is None


def _cyclic_container() -> Container:
    rules = RuleSet(
        {
            "a": Rule("a", lambda b: b, deps=("b",)),
            "b": Rule("b", lambda a: a, deps=("a",)),
        },
        {"a": ("b",), "b": ("a",)},
    )
    return Container(ContainerConfig(rules))


def test_visualize_marks_cycles_mermaid() -> None:
    container = _cyclic_container()

    out = container.visualize(format="mermaid")
    assert "[CYCLE]" in out


def test_visualize_marks_cycles_graphviz() -> None:
    container = _cyclic_container()

    out = container.visualize(format="graphviz")
    assert '[label="[CYCLE]"]' in out


def test_visualize_marks_cycles_json() -> None:
    container = _cyclic_container()

    out = container.visualize(format="json")
    assert out["a"]["in_cycle"] is True
    assert out["b"]["in_cycle"] is True


def test_visualize_unknown_format_raises() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with pytest.raises(ValueError, match="Unsupported visualize format"):
        container.visualize(format="dot")


def test_visualize_cached_until_change() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    first = container.visualize()
    second = container.visualize()
    assert first == second


def test_visualize_cache_invalidated_on_rule_change() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    before = container.visualize(format="json")
    builder.value("y", 2)
    after = container.visualize(format="json")

    assert "y" not in before
    assert "y" in after


def test_visualize_skips_unregistered_deps() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.visualize(format="json")
    assert out["a"]["deps"] == []


def test_visualize_mermaid_skips_unregistered_dep_edge() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.visualize(format="mermaid")
    assert "a --> b" not in out


def test_visualize_graphviz_skips_unregistered_dep_edge() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.visualize(format="graphviz")
    assert '"a" -> "b"' not in out


def test_visualize_mermaid_dedupes_repeated_edge() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.service("a", lambda x: x, deps=["x", "x"])
    container = builder.build()

    out = container.visualize(format="mermaid")
    assert out.count("a --> x") == 1


def test_visualize_graphviz_dedupes_repeated_edge() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.service("a", lambda x: x, deps=["x", "x"])
    container = builder.build()

    out = container.visualize(format="graphviz")
    assert out.count('"a" -> "x";') == 1


def test_build_model_returns_deps_lifetime_scope() -> None:
    rules = RuleSet(
        {
            "a": Rule(
                "a",
                lambda b: b,
                deps=("b",),
                lifetime="singleton",
                scope="app",
            ),
            "b": Rule("b", lambda: 1),
        },
        {"a": ("b",), "b": ()},
    )

    model = build_model(rules)
    assert model["a"]["deps"] == ["b"]
    assert model["a"]["lifetime"] == "singleton"
    assert model["a"]["scope"] == "app"
