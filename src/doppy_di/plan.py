"""Compile/plan mode: pre-computed dependency graph execution.

``Container.compile()`` builds an :class:`ExecutionPlan` that captures a
topological ordering of the registered rules. The plan is immutable and can be
replayed many times without re-walking the graph on every ``get``.

The feature is fully opt-in: if ``compile()`` is never called, there is zero
overhead and behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, cast

from .container import (
    CompositeRuleSet,
    Container,
    DependencyCycleError,
    Key,
    MissingDependencyError,
    Rule,
    RuleSetProtocol,
    ServiceNotFoundError,
)

logger = logging.getLogger("doppy_di.plan")


def _key_repr(key: Key) -> str:
    """Return a stable, JSON-safe string representation of a key."""
    return repr(key)


def _topological_order(
    ruleset: RuleSetProtocol, scope: Dict[Key, Rule]
) -> Tuple[List[str], Dict[str, Tuple[str, ...]]]:
    """Return (order, edges) over the registered ``scope`` of rules.

    Uses Kahn's algorithm. Raises :class:`DependencyCycleError` if a cycle is
    reachable among registered keys. Edges only point to registered dependencies.
    """
    keys = tuple(scope.keys())
    key_reprs = [_key_repr(k) for k in keys]

    indegree: Dict[str, int] = {}
    dependents: Dict[str, List[str]] = {r: [] for r in key_reprs}
    edges: Dict[str, Tuple[str, ...]] = {}

    for key in keys:
        repr_key = _key_repr(key)
        registered_deps = [d for d in ruleset.deps_of(key) if d in scope]
        edges[repr_key] = tuple(_key_repr(d) for d in registered_deps)
        indegree[repr_key] = len(registered_deps)
        for dep in registered_deps:
            dependents[_key_repr(dep)].append(repr_key)

    ready = sorted(r for r in key_reprs if indegree[r] == 0)
    order: List[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        next_ready: List[str] = []
        for dependent in dependents[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                next_ready.append(dependent)
        ready.extend(sorted(next_ready))
        ready.sort()

    if len(order) != len(keys):
        remaining = [k for k in keys if _key_repr(k) not in order]
        raise DependencyCycleError(list(remaining))
    return order, edges


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable, pre-compiled execution plan for a container.

    The plan holds a topological ordering of the registered rules plus the
    resolved dependency edges. ``get`` walks the precomputed order and delegates
    to the underlying container, so lifetimes, caches and scopes keep the same
    semantics as ``Container.get``.

    The plan is immutable: once built it cannot be changed. It may be
    serialized for caching or cross-process reuse via :meth:`serialize`.
    """

    container: Optional[Container]
    order: Tuple[str, ...]
    edges: Dict[str, Tuple[str, ...]]
    rules: Dict[str, Dict[str, Any]]
    keys: Dict[str, Key]
    singletons: Dict[str, Any] = field(default_factory=dict)
    compile_policy: str = "allow_override"

    def _resolve_ordered(self, lookup: Key) -> None:
        """Ensure dependencies of ``lookup`` resolve in topological order."""
        container = self.container
        if container is None:
            return
        from .resolution import LazyPolicy

        if isinstance(container._policy, LazyPolicy):
            return
        lookup_repr = _key_repr(lookup)
        if lookup_repr not in self.order:
            return
        idx = self.order.index(lookup_repr)
        for dep_repr in self.order[:idx]:
            dep_key = self.keys.get(dep_repr)
            if dep_key is None:
                continue
            if dep_key not in container.single:
                try:
                    container.get(dep_key)
                except ServiceNotFoundError:
                    # Lazy / injectable keys not present at compile time.
                    continue

    def get(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Resolve ``key`` using the precomputed order.

        Semantics match ``container.get(key)``: singletons share the container
        cache, scopes are honoured, overrides are applied.
        """
        lookup = (key, qualifier) if qualifier is not None else key
        self._resolve_ordered(lookup)
        container = self.container
        if container is not None:
            return container.get(lookup)
        lookup_repr = _key_repr(lookup)
        if lookup_repr in self.singletons:
            return self.singletons[lookup_repr]
        raise ServiceNotFoundError(lookup)

    def aget(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Async resolution using the precomputed order.

        Note: ``ExecutionPlan`` does not track async yield-provider resources,
        so this delegates to ``container.aget`` directly for async keys.
        """
        lookup = (key, qualifier) if qualifier is not None else key
        container = self.container
        if container is None:
            raise ServiceNotFoundError(lookup)
        self._resolve_ordered(lookup)
        return container.aget(lookup)

    @classmethod
    def from_container(
        cls, container: Container, copy_parent_rules: bool = True
    ) -> "ExecutionPlan":
        """Build an :class:`ExecutionPlan` from a container.

        Performs full static validation (missing dependencies, cycles) and
        captures a topological ordering from a snapshot of the rule set.
        Raises :class:`MissingDependencyError` for unregistered dependencies
        and :class:`DependencyCycleError` for cycles.
        """
        ruleset = container.config.ruleset

        errors: List[Tuple[Key, Key]] = []
        for key, rule in ruleset.map.items():
            for dep in rule.deps:
                if dep not in ruleset.map:
                    errors.append((key, dep))
        if errors:
            raise MissingDependencyError(
                errors[0][0],
                resolution_path=[errors[0][0], errors[0][1]],
            ) from None

        for key in ruleset.map:
            try:
                ruleset._check_cycle(key)
            except DependencyCycleError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise DependencyCycleError([key]) from exc

        if copy_parent_rules and isinstance(ruleset, CompositeRuleSet):
            rules_map: Dict[Key, Rule] = dict(ruleset.map)
        else:
            rules_map = ruleset.map

        order, edges = _topological_order(ruleset, rules_map)
        meta: Dict[str, Dict[str, Any]] = {}
        keys: Dict[str, Key] = {}
        for key in rules_map:
            repr_key = _key_repr(key)
            meta[repr_key] = _rule_meta(rules_map[key])
            keys[repr_key] = key
        policy = container.config.compile_policy.value
        return cls(
            container=container,
            order=tuple(order),
            edges=edges,
            rules=meta,
            keys=keys,
            singletons={},
            compile_policy=policy,
        )

    def _singleton_snapshot(self) -> Dict[str, Any]:
        """Capture resolved singletons (+ unresolved singleton constants)."""
        container = self.container
        if container is None:
            return dict(self.singletons)
        snapshot: Dict[str, Any] = {_key_repr(k): v for k, v in container.single.items()}
        for repr_key, meta in self.rules.items():
            if meta.get("lifetime") != "singleton":
                continue
            if repr_key in snapshot:
                continue
            key = self.keys.get(repr_key)
            if key is None:
                continue
            try:
                snapshot[repr_key] = container.get(key)
            except Exception as exc:
                logger.debug("singleton %r not resolvable for snapshot: %s", repr_key, exc)
        return snapshot

    def serialize(self, format: str = "json") -> str:  # noqa: A002
        """Serialize the plan to a string for caching or cross-process use.

        Only the graph topology, rule metadata and resolved singletons are
        persisted. Factory callables are not serialized (they must be
        module-level for true cross-process reuse).
        """
        if format != "json":
            raise ValueError(f"Unsupported serialize format: {format!r}")
        payload = {
            "order": list(self.order),
            "edges": self.edges,
            "rules": self.rules,
            "keys": {rk: _key_to_serializable(k) for rk, k in self.keys.items()},
            "singletons": {
                rk: _value_to_serializable(v) for rk, v in self._singleton_snapshot().items()
            },
            "policy": self.compile_policy,
        }
        return json.dumps(payload, sort_keys=True, indent=2)

    @classmethod
    def deserialize(cls, data: str) -> "ExecutionPlan":
        """Rebuild an :class:`ExecutionPlan` from serialized data."""
        payload = json.loads(data)
        keys: Dict[str, Key] = {
            rk: cast(Key, _key_from_serializable(v)) for rk, v in payload["keys"].items()
        }
        singletons: Dict[str, Any] = {
            rk: _value_from_serializable(v) for rk, v in payload.get("singletons", {}).items()
        }
        return cls(
            container=None,
            order=tuple(payload["order"]),
            edges=payload["edges"],
            rules=payload["rules"],
            keys=keys,
            singletons=singletons,
            compile_policy=payload.get("policy", "allow_override"),
        )


def _rule_meta(rule: Rule) -> Dict[str, Any]:
    return {
        "lifetime": rule.lifetime,
        "deps": [repr(d) for d in rule.deps],
        "scope": rule.scope,
        "yield": bool(rule.yield_provider or rule.async_yield_provider),
        "async": bool(rule.async_yield_provider),
        "nested": bool(rule.nested),
        "is_async": bool(rule.is_async),
    }


def _key_to_serializable(key: Key) -> Any:
    """Convert a key into a JSON-serializable marker."""
    if isinstance(key, str):
        return {"__str__": key}
    if isinstance(key, type):
        return {"__type__": f"{key.__module__}.{key.__qualname__}"}
    if isinstance(key, tuple):
        return {"__tuple__": [_key_to_serializable(k) for k in key]}
    return {"__str__": repr(key)}


def _key_from_serializable(obj: Any) -> Any:
    if isinstance(obj, dict) and "__str__" in obj and len(obj) == 1:
        return obj["__str__"]
    result: Any = obj
    if isinstance(result, dict) and "__type__" in result:
        type_path = result["__type__"]
        module, _, qualname = type_path.rpartition(".")
        mod = __import__(module, fromlist=[qualname])
        result = getattr(mod, qualname)
    if isinstance(result, dict) and "__tuple__" in result:
        result = tuple(_key_from_serializable(k) for k in result["__tuple__"])
    return result


def _value_to_serializable(value: Any) -> Any:
    """Best-effort serialization wrapper for singleton values."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return {"__literal__": value}
    return {"__repr__": repr(value)}


def _value_from_serializable(obj: Any) -> Any:
    if isinstance(obj, dict) and "__literal__" in obj:
        return obj["__literal__"]
    if isinstance(obj, dict) and "__repr__" in obj:
        return obj["__repr__"]
    return obj
