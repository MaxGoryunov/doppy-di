"""Tests for graph introspection API and CLI (issue #35)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from doppy_di import Container, ContainerBuilder
from doppy_di.cli import _get_container, _load_container_or_builder, cli
from doppy_di.container import (
    DuplicateRegistrationError,
    ServiceNotFoundError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> Any:
    path = FIXTURES / name
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_graph_nodes_and_edges() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    g = container.graph()
    assert "a" in g.nodes()
    assert "b" in g.nodes()
    assert ("a", "b") in g.edges()


def test_dependencies_and_dependents() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    g = container.graph()
    assert g.dependencies_of("a") == ("b",)
    assert g.dependents_of("b") == ("a",)


def test_graph_export_mermaid() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.graph().to_mermaid()
    assert "graph TD" in out
    assert "a --> b" in out


def test_graph_export_dot() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.graph().to_dot()
    assert "digraph G" in out
    assert '"a" -> "b";' in out


def test_graph_export_json() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.graph().to_json()
    assert out["a"]["deps"] == ["b"]
    assert out["b"]["lifetime"] == "singleton"


def test_graph_export_text() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    out = container.graph().to_text()
    assert "a" in out
    assert "b" in out


def test_graph_dependencies_of_missing_raises() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    container = builder.build()

    with pytest.raises(ServiceNotFoundError):
        container.graph().dependencies_of("missing")


def test_graph_dependents_of_missing_raises() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    container = builder.build()

    with pytest.raises(ServiceNotFoundError):
        container.graph().dependents_of("missing")


def test_graph_zero_overhead() -> None:
    builder = ContainerBuilder()
    builder.value("b", 1)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    container.graph()
    assert container.single == {}


def test_graph_cycle_handling() -> None:
    builder = ContainerBuilder(check_cycles_on_register=False)
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()
    out = container.graph().to_text()
    assert "cycle" in out


def test_graph_skips_unregistered_dep_edge() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    g = container.graph()
    assert g.edges() == ()


def test_cli_explain() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["explain", "service", "--file", str(FIXTURES / "good_container.py")]
    )
    assert result.exit_code == 0
    assert "service" in result.output


def test_cli_explain_missing_key() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "nope", "--file", str(FIXTURES / "good_container.py")])
    assert result.exit_code == 1


def test_cli_check_detects_missing() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "bad_container.py")])
    assert result.exit_code == 1
    assert "missing" in result.output.lower()


def test_cli_check_ok() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "good_container.py")])
    assert result.exit_code == 0


def test_cli_graph_mermaid() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["graph", str(FIXTURES / "good_container.py"), "--format", "mermaid"]
    )
    assert result.exit_code == 0
    assert "graph TD" in result.output


def test_cli_graph_dot() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "good_container.py"), "--format", "dot"])
    assert result.exit_code == 0
    assert "digraph G" in result.output


def test_cli_graph_json() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "good_container.py"), "--format", "json"])
    assert result.exit_code == 0
    assert '"service"' in result.output


def test_cli_graph_text() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "good_container.py"), "--format", "text"])
    assert result.exit_code == 0
    assert "service" in result.output


def test_cli_graph_bad_format() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "good_container.py"), "--format", "nope"])
    assert result.exit_code == 2


def test_cli_check_cycles() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "cycle_container.py")])
    assert result.exit_code == 1
    assert "cycle" in result.output.lower()


def test_cli_check_unused_with_roots() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "check",
            str(FIXTURES / "unused_container.py"),
            "--root",
            "service",
        ],
    )
    assert result.exit_code == 0
    assert "unused" in result.output.lower()


def test_cli_check_unused_strict() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "check",
            str(FIXTURES / "unused_container.py"),
            "--root",
            "service",
            "--strict",
        ],
    )
    assert result.exit_code == 1
    assert "unused" in result.output.lower()


def test_cli_check_no_roots_nothing_unused() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "unused_container.py")])
    assert result.exit_code == 0
    assert "unused" not in result.output.lower()


def test_cli_check_lifetime_violation() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "lifetime_container.py")])
    assert result.exit_code == 1
    assert "lifetime" in result.output.lower()


def test_cli_check_duplicate_sources() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "duplicate_container.py")])
    assert result.exit_code == 1
    assert "duplicate" in result.output.lower()


def test_cli_missing_file() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "nope.py")])
    assert result.exit_code == 2


def test_cli_no_container_symbol() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "no_container.py")])
    assert result.exit_code == 1


def test_cli_missing_file_explain() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "x", "--file", str(FIXTURES / "nope.py")])
    assert result.exit_code == 2


def test_cli_runtime_error_load() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(FIXTURES / "runtime_error_container.py")])
    assert result.exit_code == 1
    assert "error loading" in result.output.lower()


def test_cli_build_callable() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "build_callable_container.py")])
    assert result.exit_code == 0
    assert "db" in result.output


def test_cli_builder_container() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "builder_container.py")])
    assert result.exit_code == 0
    assert "db" in result.output


def test_cli_scan_container() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["graph", str(FIXTURES / "scan_container.py")])
    assert result.exit_code == 0
    assert "db" in result.output


def test_cli_explain_scoped() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["explain", "session", "--file", str(FIXTURES / "scoped_container.py")]
    )
    assert result.exit_code == 0
    assert "Scope: request" in result.output


def test_cli_explain_no_deps() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["explain", "db", "--file", str(FIXTURES / "good_container.py")])
    assert result.exit_code == 0
    assert "Dependencies: none" in result.output
    assert "Dependents:" in result.output


def test_get_container_builder() -> None:
    builder = ContainerBuilder()
    builder.value("db", 1)
    container = _get_container(builder)
    assert isinstance(container, Container)


def test_get_container_duplicate_error() -> None:
    err = DuplicateRegistrationError("x")
    with pytest.raises(DuplicateRegistrationError):
        _get_container(err)


def test_get_container_invalid() -> None:
    with pytest.raises(click.ClickException):
        _get_container(object())


def test_load_container_duplicate_error() -> None:
    obj = _load_container_or_builder(str(FIXTURES / "duplicate_container.py"))
    assert isinstance(obj, DuplicateRegistrationError)
