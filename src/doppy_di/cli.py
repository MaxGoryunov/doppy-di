"""Command-line interface for doppy-di."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, List, Set

import click

from .container import Container, ContainerBuilder, DuplicateRegistrationError, Key


def _import_module(path: Path, module_name: str) -> Any:
    """Import ``path`` as a module, returning a ``DuplicateRegistrationError`` if raised."""
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
    return module


def _find_container(module: Any) -> Any:
    """Return the container, builder, or build callable defined in ``module``."""
    for attr in ("container", "builder", "build"):
        if hasattr(module, attr):
            obj = getattr(module, attr)
            if attr == "build" and callable(obj):
                return obj()
            return obj

    # scan module for any Container or ContainerBuilder instances
    for value in module.__dict__.values():  # NOSONAR(S7504) dict view iterable
        if isinstance(value, (Container, ContainerBuilder)):
            return value

    return None


def _load_container_or_builder(file_path: str) -> Any:
    """Load container, builder, or build callable from file."""
    path = Path(file_path).resolve()
    if not path.exists():
        click.echo(f"Error: File not found: {path}", err=True)
        sys.exit(1)

    sys.path.insert(0, str(path.parent))
    module_name = path.stem

    module = _import_module(path, module_name)
    if isinstance(module, DuplicateRegistrationError):
        return module
    obj = _find_container(module)
    if obj is not None:
        return obj

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
    matched_key = _match_node(g, key)

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


def _match_node(g: Any, key: str) -> Any:
    """Return the graph node matching ``key`` by ``str`` or ``repr``, or ``None``."""
    for node in g.nodes():
        if str(node) == key or repr(node) == key:
            return node
    return None


def _collect_missing_deps(ruleset: Any) -> List[tuple[Key, Key]]:
    """Return ``(key, dep)`` pairs whose dependency is unregistered."""
    missing: List[tuple[Key, Key]] = []
    for key, rule in ruleset.map.items():
        for dep in rule.deps:
            if dep not in ruleset.map:
                missing.append((key, dep))
    return missing


def _cycle_path(exc: Exception) -> List[Key] | None:
    """Extract a cycle path from a cycle exception, if present."""
    # CycleError or DependencyCycleError path
    path = getattr(exc, "path", None) or getattr(exc, "cycle", None)
    if path:
        return list(path)
    return None


def _collect_cycles(ruleset: Any) -> List[List[Key]]:
    """Return dependency cycles discovered in ``ruleset``."""
    cycles: List[List[Key]] = []
    for key in ruleset.map:
        try:
            ruleset._check_cycle(key)
        except Exception as exc:
            path = _cycle_path(exc)
            if path is not None and path not in cycles:
                cycles.append(path)
    return cycles


def _reachable_from(ruleset: Any, matched_roots: Set[Key]) -> Set[Key]:
    """BFS over ``ruleset.map`` starting from ``matched_roots``."""
    reachable: Set[Key] = set()
    queue = list(matched_roots)
    while queue:
        curr = queue.pop(0)
        if curr in reachable:
            continue
        reachable.add(curr)
        rule_obj = ruleset.map[curr]
        if rule_obj:
            for dep in rule_obj.deps:
                if dep in ruleset.map and dep not in reachable:
                    queue.append(dep)
    return reachable


def _collect_unused(g: Any, ruleset: Any, roots: List[str]) -> Set[Key]:
    """Return registered keys not reachable from any of ``roots``."""
    matched_roots: Set[Key] = set()
    for root_str in roots:
        node = _match_node(g, root_str)
        if node is not None:
            matched_roots.add(node)

    return set(g.nodes()) - _reachable_from(ruleset, matched_roots)


def _report_lifetime_violations(ruleset: Any) -> bool:
    """Report singleton rules with local scopes. Returns ``True`` if found."""
    found = False
    for key, rule_obj in ruleset.map.items():
        if rule_obj.lifetime == "singleton" and rule_obj.scope:
            found = True
            click.echo(
                f"ERROR: Lifetime violation: Singleton {key!r} "
                f"cannot have a local scope: {rule_obj.scope}",
                err=True,
            )
    return found


def _load_container_for_check(file: str) -> Any:
    """Load the container for ``check``, exiting with an error message on failure."""
    try:
        obj = _load_container_or_builder(file)
        return _get_container(obj)
    except DuplicateRegistrationError as exc:
        click.echo(f"ERROR: Duplicate Registration detected:\n{exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"ERROR: Loading failed: {exc}", err=True)
        sys.exit(1)


def _report_missing(ruleset: Any) -> bool:
    """Report unregistered dependencies. Returns ``True`` if any were found."""
    found = False
    for key, dep in _collect_missing_deps(ruleset):
        found = True
        click.echo(f"ERROR: Missing dependency: {key!r} depends on unregistered {dep!r}", err=True)
    return found


def _report_cycles(ruleset: Any) -> bool:
    """Report dependency cycles. Returns ``True`` if any were found."""
    found = False
    for cyc in _collect_cycles(ruleset):
        found = True
        path_str = " -> ".join(map(repr, cyc))
        click.echo(f"ERROR: Dependency cycle detected: {path_str}", err=True)
    return found


def _report_unused(g: Any, ruleset: Any, roots: List[str]) -> bool:
    """Report registrations unreachable from ``roots``. Returns ``True`` if found."""
    found = False
    for key in sorted(_collect_unused(g, ruleset, roots), key=lambda k: str(k)):
        found = True
        click.echo(
            f"WARNING: Unused registration: {key!r} is not reachable from any root",
            err=True,
        )
    return found


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
    container = _load_container_for_check(file)
    ruleset = container.config.ruleset
    g = container.graph()

    # 1. Missing dependencies
    has_errors = _report_missing(ruleset)

    # 2. Cycles
    has_errors = _report_cycles(ruleset) or has_errors

    # 3. Duplicate keys with track_sources
    # (Already handled by DuplicateRegistrationError above if FAIL policy is used,
    # but we can also check RegistrationSources if track_sources was enabled)
    # Actually, DuplicateRegistrationError was raised on load, which is sufficient.

    # 4. Unused registrations (reachability)
    has_warnings = _report_unused(g, ruleset, list(roots)) if roots else False

    # 5. Lifetime violations
    # Check: singleton rule cannot be overridden by scoped dependency
    if _report_lifetime_violations(ruleset):
        has_errors = True

    if has_errors or (has_warnings and strict):
        sys.exit(1)
    sys.exit(0)
