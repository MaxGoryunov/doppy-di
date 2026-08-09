"""Dependency graph rendering for :meth:`doppy_di.Container.visualize`.

Renders registered rules as mermaid, Graphviz dot, or a JSON dict.
Lifetime is encoded as node color, optional scope as node shape. Edges
belonging to a dependency cycle are annotated ``[CYCLE]``.

Example:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("db", object())
    >>> builder.service("service", lambda db: db, deps=["db"])
    >>> container = builder.build()
    >>> "graph TD" in container.visualize("mermaid")
    True
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from ..container import Key, RuleSet


def _display(key: Key) -> str:
    """Return a printable label for a rule key."""
    return str(key)


def _safe_id(label: str) -> str:
    """Sanitize a label into a valid mermaid node id."""
    return re.sub(r"\W+", "_", label) or "node"


def build_model(ruleset: RuleSet) -> Dict[str, Dict[str, Any]]:
    """Build a dependency model from a rule set.

    Returns a dict keyed by display label with ``deps``, ``lifetime`` and
    optional ``scope`` for each registered rule.
    """
    model: Dict[str, Dict[str, Any]] = {}
    for key, rule in ruleset.map.items():
        label = _display(key)
        model[label] = {
            "deps": [_display(dep) for dep in rule.deps],
            "lifetime": rule.lifetime,
            "scope": rule.scope,
        }
    return model


def _cycle_edges(ruleset: RuleSet) -> Set[Tuple[Key, Key]]:
    """Return the set of edges that participate in a dependency cycle."""
    index: Dict[Key, int] = {}
    lowlink: Dict[Key, int] = {}
    on_stack: Set[Key] = set()
    stack: List[Key] = []
    edges: Set[Tuple[Key, Key]] = set()
    counter = [0]

    def strongconnect(node: Key) -> None:
        index[node] = counter[0]
        lowlink[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for dep in ruleset.graph.get(node, ()):
            if dep not in ruleset.map:
                continue
            if dep not in index:
                strongconnect(dep)
                lowlink[node] = min(lowlink[node], lowlink[dep])
            elif dep in on_stack:
                lowlink[node] = min(lowlink[node], index[dep])

        if lowlink[node] == index[node]:
            component: List[Key] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                members = set(component)
                for member in component:
                    for dep in ruleset.graph.get(member, ()):
                        if dep in members:
                            edges.add((member, dep))

    for key in ruleset.map:
        if key not in index:
            strongconnect(key)
    return edges


def _lifetime_class(lifetime: str) -> str:
    return "singleton" if lifetime == "singleton" else "transient"


def render_mermaid(ruleset: RuleSet) -> str:
    """Render the dependency graph as mermaid ``graph TD``."""
    lines = ["graph TD"]
    for key, rule in ruleset.map.items():
        label = _display(key)
        node_id = _safe_id(label)
        if rule.scope:
            lines.append(f"    {node_id}([{label}]){_lifetime_class(rule.lifetime)}")
        else:
            lines.append(f"    {node_id}[{label}]{_lifetime_class(rule.lifetime)}")
    lines.append("    classDef singleton fill:#90EE90,stroke:#333,stroke-width:1px;")
    lines.append("    classDef transient fill:#ADD8E6,stroke:#333,stroke-width:1px;")

    cyclic = _cycle_edges(ruleset)
    seen: Set[Tuple[str, str]] = set()
    for key, rule in ruleset.map.items():
        src = _safe_id(_display(key))
        for dep in rule.deps:
            if dep not in ruleset.map:
                continue
            dst = _safe_id(_display(dep))
            if (src, dst) in seen:
                continue
            seen.add((src, dst))
            if (key, dep) in cyclic:
                lines.append(f"    {src} -->|{_display(dep)} [CYCLE]| {dst}")
            else:
                lines.append(f"    {src} --> {dst}")
    return "\n".join(lines)


def render_graphviz(ruleset: RuleSet) -> str:
    """Render the dependency graph as Graphviz ``digraph``."""
    lines = ["digraph G {"]
    for key, rule in ruleset.map.items():
        label = _display(key)
        shape = "stadium" if rule.scope else "box"
        lines.append(f'    "{label}" [shape={shape}, color={_lifetime_class(rule.lifetime)}];')

    cyclic = _cycle_edges(ruleset)
    seen: Set[Tuple[str, str]] = set()
    for key, rule in ruleset.map.items():
        src = _display(key)
        for dep in rule.deps:
            if dep not in ruleset.map:
                continue
            dst = _display(dep)
            if (src, dst) in seen:
                continue
            seen.add((src, dst))
            if (key, dep) in cyclic:
                lines.append(f'    "{src}" -> "{dst}" [label="[CYCLE]"];')
            else:
                lines.append(f'    "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def render_json(ruleset: RuleSet) -> Dict[str, Any]:
    """Render the dependency graph as a structured JSON dict."""
    cyclic = _cycle_edges(ruleset)
    data: Dict[str, Any] = {}
    for key, rule in ruleset.map.items():
        label = _display(key)
        data[label] = {
            "deps": [_display(dep) for dep in rule.deps if dep in ruleset.map],
            "lifetime": rule.lifetime,
            "scope": rule.scope,
            "in_cycle": any((key, dep) in cyclic for dep in rule.deps),
        }
    return data


def render(ruleset: RuleSet, format: str) -> Any:  # noqa: A002
    """Render the rule set in the requested format.

    Raises:
        ValueError: If ``format`` is not supported.
    """
    if format == "mermaid":
        return render_mermaid(ruleset)
    if format == "graphviz":
        return render_graphviz(ruleset)
    if format == "json":
        return render_json(ruleset)
    raise ValueError(f"Unsupported visualize format: {format!r}")
