"""Graph representation of the container dependency structure."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from .container import Key, RuleSetProtocol, ServiceNotFoundError
from .devkit.visualize import render_graphviz, render_json, render_mermaid


class DependencyGraph:
    """DependencyGraph for container introspection."""

    def __init__(self, ruleset: RuleSetProtocol) -> None:
        self._ruleset = ruleset

    def nodes(self) -> Tuple[Key, ...]:
        """Return all registered keys in the graph."""
        return tuple(self._ruleset.map.keys())

    def edges(self) -> Tuple[Tuple[Key, Key], ...]:
        """Return all directed edges (key, dependency).

        Only returns edges pointing to registered dependencies.
        """
        res: List[Tuple[Key, Key]] = []
        for key, rule in self._ruleset.map.items():
            for dep in rule.deps:
                if dep in self._ruleset.map:
                    res.append((key, dep))
        return tuple(res)

    def dependencies_of(self, key: Key) -> Tuple[Key, ...]:
        """Return registered direct dependencies of key."""
        if key not in self._ruleset.map:
            raise ServiceNotFoundError(key)
        rule = self._ruleset.map[key]
        return tuple(d for d in rule.deps if d in self._ruleset.map)

    def dependents_of(self, key: Key) -> Tuple[Key, ...]:
        """Return registered direct dependents of key."""
        if key not in self._ruleset.map:
            raise ServiceNotFoundError(key)
        res: List[Key] = []
        for k, rule in self._ruleset.map.items():
            if key in rule.deps:
                res.append(k)
        return tuple(res)

    def to_mermaid(self) -> str:
        """Render graph as mermaid graph."""
        return render_mermaid(self._ruleset)

    def to_dot(self) -> str:
        """Render graph as graphviz dot string."""
        return render_graphviz(self._ruleset)

    def to_json(self) -> Dict[str, Any]:
        """Render graph as JSON dictionary."""
        return render_json(self._ruleset)

    def to_text(self) -> str:
        """Render graph as text tree."""
        lines: List[str] = []
        visited: Set[Key] = set()

        roots = [k for k in self.nodes() if not self.dependents_of(k)]
        if not roots:
            roots = list(self.nodes())

        self._draw_roots(lines, visited, roots)
        return "\n".join(lines)

    def _draw_node(
        self, lines: List[str], visited: Set[Key], node: Key, prefix: str, is_last: bool
    ) -> None:
        """Draw one node and its dependency subtree."""
        visited.add(node)
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{node!r}")
        deps = self.dependencies_of(node)
        for i, dep in enumerate(deps):
            new_prefix = prefix + ("    " if is_last else "│   ")
            if dep not in visited:
                self._draw_node(lines, visited, dep, new_prefix, i == len(deps) - 1)
            else:
                marker = "└── " if i == len(deps) - 1 else "├── "
                lines.append(f"{new_prefix}{marker}{dep!r} (cycle)")

    def _draw_roots(
        self,
        lines: List[str],
        visited: Set[Key],
        roots: List[Key],
    ) -> None:
        """Draw each unvisited ``root`` and its dependency subtree."""
        for root in roots:
            if root in visited:
                continue
            lines.append(f"{root!r}")
            deps = self.dependencies_of(root)
            for i, dep in enumerate(deps):
                self._draw_node(lines, visited, dep, "", i == len(deps) - 1)
