"""Pluggable resolution policies.

Resolution policies control the order in which dependency keys are resolved.
A policy receives the rule graph and the requested root key, and returns an
iteration order of keys. The container honours that order when resolving.

The feature is fully opt-in: if no policy is specified, the default
resolution path is used with zero extra dispatch.

Examples:
    >>> from doppy_di.container import ContainerBuilder
    >>> from doppy_di.resolution import ChildrenFirstPolicy
    >>> builder = ContainerBuilder()
    >>> builder.value("b", 1)
    >>> builder.service("a", lambda b: b, deps=["b"])
    >>> container = builder.build(policy=ChildrenFirstPolicy())
    >>> container.get("a")
    1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Protocol, runtime_checkable

from .container import Key, Rule


@runtime_checkable
class ResolutionPolicy(Protocol):
    """A strategy for ordering dependency resolution.

    Implementations receive the rule graph and the requested root key and
    return an iteration order of keys. The container resolves keys in that
    order.

    Examples:
        >>> class ReversePolicy:
        ...     def order(self, graph, root):
        ...         return reversed(list(graph.keys()))
        >>> isinstance(ReversePolicy(), ResolutionPolicy)
        True
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        """Return the resolution order of keys for ``root``."""
        ...


def _reachable(
    graph: Mapping[Key, Rule],
    root: Key,
) -> List[Key]:
    """Return keys reachable from ``root`` via declared dependencies."""
    seen: List[Key] = []
    stack = [root]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.append(key)
        rule = graph.get(key)
        if rule is not None:
            stack.extend(rule.deps)
    return seen


def _topological(
    graph: Mapping[Key, Rule],
    keys: Iterable[Key],
) -> List[Key]:
    """Return ``keys`` in dependency order (dependencies first)."""
    scope = list(keys)
    indegree: Dict[Key, int] = dict.fromkeys(scope, 0)
    dependents: Dict[Key, List[Key]] = {key: [] for key in scope}
    for key in scope:
        rule = graph.get(key)
        if rule is None:
            continue
        for dep in rule.deps:
            if dep in indegree:
                indegree[key] += 1
                dependents[dep].append(key)

    ready = [key for key in scope if indegree[key] == 0]
    order: List[Key] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for dependent in dependents[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    # Keys not reachable through registered deps keep their original order.
    for key in scope:
        if key not in order:
            order.append(key)
    return order


@dataclass(frozen=True)
class DefaultResolutionPolicy:
    """Default resolution order: resolve only the requested key.

    Matches the historical container behaviour exactly. Dependencies are
    resolved recursively on demand; nothing is pre-resolved.

    Examples:
        >>> policy = DefaultResolutionPolicy()
        >>> list(policy.order({}, "a"))
        ['a']
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return (root,)


@dataclass(frozen=True)
class LazyPolicy:
    """Resolve only the requested key on demand.

    This is the same semantics as the default behaviour: nothing is resolved
    until ``get()`` is called, and only the requested key plus its transitive
    dependencies are resolved.

    Examples:
        >>> policy = LazyPolicy()
        >>> list(policy.order({}, "a"))
        ['a']
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return (root,)


@dataclass(frozen=True)
class ParentFirstPolicy:
    """Resolve parents before children.

    The requested root (parent) is attempted first; its transitive
    dependencies (children) follow.

    Examples:
        >>> builder = ContainerBuilder()
        >>> builder.value("b", 1)
        >>> builder.service("a", lambda b: b, deps=["b"])
        >>> container = builder.build(policy=ParentFirstPolicy())
        >>> container.get("a")
        1
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return reversed(_topological(graph, _reachable(graph, root)))


@dataclass(frozen=True)
class ChildrenFirstPolicy:
    """Resolve children before parents.

    Transitive dependencies (children) are resolved before the requested
    root (parent).

    Examples:
        >>> builder = ContainerBuilder()
        >>> builder.value("b", 1)
        >>> builder.service("a", lambda b: b, deps=["b"])
        >>> container = builder.build(policy=ChildrenFirstPolicy())
        >>> container.get("a")
        1
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return _topological(graph, _reachable(graph, root))


@dataclass(frozen=True)
class EagerPolicy:
    """Resolve the entire graph eagerly.

    All registered keys are resolved up front. When used with ``build()``,
    every sync rule is resolved at build time. Async rules cannot be resolved
    in a sync context and raise :class:`AsyncDependencyInSyncContextError`.

    Examples:
        >>> builder = ContainerBuilder()
        >>> builder.value("a", 1)
        >>> container = builder.build(policy=EagerPolicy())
        >>> container.get("a")
        1
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return _topological(graph, graph.keys())


@dataclass(frozen=True)
class ParallelPolicy:
    """Resolve independent branches concurrently (async only).

    The policy orders keys by dependency level. In ``aget()`` each level is
    resolved concurrently with ``asyncio.gather``. In sync ``get()`` the
    order is honoured sequentially, because there is no event loop.

    Examples:
        >>> policy = ParallelPolicy()
        >>> list(policy.order({}, "a"))
        ['a']
    """

    def order(
        self,
        graph: Mapping[Key, Rule],
        root: Key,
    ) -> Iterable[Key]:
        return _topological(graph, _reachable(graph, root))
