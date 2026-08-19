"""Command-line interface for doppy-di."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, List, Set

import click

from .container import Container, ContainerBuilder, DuplicateRegistrationError, Key


def _load_container_or_builder(file_path: str) -> Any:
    """Load container, builder, or build callable from file."""
    path = Path(file_path).resolve()
    if not path.exists():
        click.echo(f"Error: File not found: {path}", err=True)
        sys.exit(1)

    sys.path.insert(0, str(path.parent))
    module_name = path.stem

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            click.echo(f"Error: Could not load {path}", err=True)
            sys.exit(1)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        # If it's a DuplicateRegistrationError, we handle it during check later,
        # but if we are just loading, bubble it up or keep reference
        if isinstance(exc, DuplicateRegistrationError):
            return exc
        click.echo(f"Error loading {path}: {exc}", err=True)
        sys.exit(1)

    for attr in ("container", "builder", "build"):
        if hasattr(module, attr):
            obj = getattr(module, attr)
            if attr == "build" and callable(obj):
                return obj()
            return obj

    # scan module for any Container or ContainerBuilder instances
    for _, value in list(module.__dict__.items()):
        if isinstance(value, Container):
            return value
        if isinstance(value, ContainerBuilder):
            return value

    click.echo(f"Error: No Container or ContainerBuilder found in {path}", err=True)
    sys.exit(1)


def _get_container(obj: Any) -> Container:
    if isinstance(obj, Container):
        return obj
    if isinstance(obj, ContainerBuilder):
        return obj.build()
    if isinstance(obj, DuplicateRegistrationError):
        raise obj
    raise click.ClickException(f"Invalid container object: {type(obj)}")


@click.group()  # type: ignore[misc]
def cli() -> None:
    """doppy-di CLI for dependency graph introspection."""


@cli.command()  # type: ignore[misc]
@click.argument("file", type=click.Path(exists=True))  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--format",
    "fmt",
    type=click.Choice(["mermaid", "dot", "json", "text"]),
    default="text",
    help="Output format.",
)
def graph(file: str, fmt: str) -> None:  # type: ignore[misc]
    """Output dependency graph representation."""
    obj = _load_container_or_builder(file)
    container = _get_container(obj)
    g = container.graph()

    if fmt == "mermaid":
        click.echo(g.to_mermaid())
    elif fmt == "dot":
        click.echo(g.to_dot())
    elif fmt == "json":
        import json

        click.echo(json.dumps(g.to_json(), indent=2))
    else:
        click.echo(g.to_text())


@cli.command()  # type: ignore[misc]
@click.argument("key")  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--file", "file_path", required=True, type=click.Path(exists=True)
)
def explain(key: str, file_path: str) -> None:  # type: ignore[misc]
    """Explain dependencies and dependents of KEY."""
    obj = _load_container_or_builder(file_path)
    container = _get_container(obj)
    g = container.graph()

    # Find the actual Key in graph that matches key string repr
    matched_key: Key | None = None
    for node in g.nodes():
        if str(node) == key or repr(node) == key:
            matched_key = node
            break

    if matched_key is None:
        click.echo(f"Key {key!r} not found in container.", err=True)
        sys.exit(1)

    click.echo(f"Key: {matched_key!r}")
    rule = container.config.ruleset.map[matched_key]
    click.echo(f"Lifetime: {rule.lifetime}")
    if rule.scope:
        click.echo(f"Scope: {rule.scope}")

    deps = g.dependencies_of(matched_key)
    if deps:
        click.echo("Dependencies:")
        for dep in deps:
            click.echo(f"  - {dep!r}")
    else:
        click.echo("Dependencies: none")

    revs = g.dependents_of(matched_key)
    if revs:
        click.echo("Dependents:")
        for rev in revs:
            click.echo(f"  - {rev!r}")
    else:
        click.echo("Dependents: none")


@cli.command()  # type: ignore[misc]
@click.argument("file", type=click.Path(exists=True))  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--root", "roots", multiple=True, help="Root keys to trace reachability from."
)
@click.option(  # type: ignore[misc]
    "--strict", is_flag=True, help="Treat warnings as errors."
)
def check(file: str, roots: List[str], strict: bool) -> None:  # type: ignore[misc]
    """Lint container configuration for issues."""
    has_errors = False
    has_warnings = False

    # Check for duplicate registration errors caught during module import
    try:
        obj = _load_container_or_builder(file)
        container = _get_container(obj)
    except DuplicateRegistrationError as exc:
        click.echo(f"ERROR: Duplicate Registration detected:\n{exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"ERROR: Loading failed: {exc}", err=True)
        sys.exit(1)

    ruleset = container.config.ruleset
    g = container.graph()

    # 1. Missing dependencies
    missing: List[tuple[Key, Key]] = []
    for key, rule in ruleset.map.items():
        for dep in rule.deps:
            if dep not in ruleset.map:
                missing.append((key, dep))

    # mypy fix for rule assignment below
    from .container import Rule as ContainerRule

    rule_obj: ContainerRule
    if missing:
        has_errors = True
        for key, dep in missing:
            click.echo(
                f"ERROR: Missing dependency: {key!r} depends on unregistered {dep!r}", err=True
            )

    # 2. Cycles
    cycles: List[List[Key]] = []
    for key in ruleset.map:
        try:
            ruleset._check_cycle(key)
        except Exception as exc:
            # CycleError or DependencyCycleError path
            path = getattr(exc, "path", None) or getattr(exc, "cycle", None)
            if path and list(path) not in cycles:
                cycles.append(list(path))
    if cycles:
        has_errors = True
        for cyc in cycles:
            path_str = " -> ".join(map(repr, cyc))
            click.echo(f"ERROR: Dependency cycle detected: {path_str}", err=True)

    # 3. Duplicate keys with track_sources
    # (Already handled by DuplicateRegistrationError above if FAIL policy is used,
    # but we can also check RegistrationSources if track_sources was enabled)
    # Actually, DuplicateRegistrationError was raised on load, which is sufficient.

    # 4. Unused registrations (reachability)
    if roots:
        # Convert root strings to matching Keys in container
        matched_roots: Set[Key] = set()
        for root_str in roots:
            for node in g.nodes():
                if str(node) == root_str or repr(node) == root_str:
                    matched_roots.add(node)

        # BFS to find reachable nodes
        reachable: Set[Key] = set()
        queue = list(matched_roots)
        while queue:
            curr = queue.pop(0)
            if curr not in reachable:
                reachable.add(curr)
                rule_obj = ruleset.map[curr]
                if rule_obj:
                    for dep in rule_obj.deps:
                        if dep in ruleset.map and dep not in reachable:
                            queue.append(dep)

        unused = set(g.nodes()) - reachable
        if unused:
            has_warnings = True
            for key in sorted(unused, key=lambda k: str(k)):
                click.echo(
                    f"WARNING: Unused registration: {key!r} is not reachable from any root",
                    err=True,
                )

    # 5. Lifetime violations
    # Check: singleton rule cannot be overridden by scoped dependency
    for key, rule_obj in ruleset.map.items():
        if rule_obj.lifetime == "singleton" and rule_obj.scope:
            has_errors = True
            click.echo(
                f"ERROR: Lifetime violation: Singleton {key!r} "
                f"cannot have a local scope: {rule_obj.scope}",
                err=True,
            )

    if has_errors or (has_warnings and strict):
        sys.exit(1)
    sys.exit(0)
