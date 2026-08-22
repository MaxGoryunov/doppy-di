"""Compile/plan mode: pre-computed dependency graph execution.

``Container.compile()`` builds an :class:`ExecutionPlan` that captures a
topological ordering of the registered rules. The plan is immutable and can be
replayed many times without re-walking the graph on every ``get``.

The feature is fully opt-in: if ``compile()`` is never called, there is zero
overhead and behaviour is unchanged.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from .container import (
    CompositeRuleSet,
    Container,
    DependencyCycleError,
    InvalidFactoryError,
    Key,
    MissingDependencyError,
    Rule,
    RuleSetProtocol,
    ServiceNotFoundError,
    _unset,
)

logger = logging.getLogger("doppy_di.plan")

_MISSING = object()


def _key_repr(key: Key) -> str:
    """Return a stable, JSON-safe string representation of a key."""
    return repr(key)


def _topological_order(
    ruleset: RuleSetProtocol, scope: Dict[Key, Rule]
) -> Tuple[List[str], Dict[str, Tuple[str, ...]]]:
    """Return (order, edges) over the registered ``scope`` of rules.

    Uses Kahn's algorithm. Raises :class:`DependencyCycleError` if a cycle is
    reachable among registered keys. Edges only point to registered deps.
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
class _NodeSpec:
    """Pre-computed per-node resolution spec for the fast path."""

    key: Key
    make: Optional[Callable[..., Any]]
    deps_idx: Tuple[int, ...]
    lifetime: str
    yield_provider: bool
    async_yield_provider: bool
    is_async: bool
    nested: bool


def _build_node_maker(
    spec: _NodeSpec,
    dep_makers: Tuple[Callable[[], Any], ...],
    container: Container,
) -> Callable[[], Any]:
    """Build a flat closure that resolves ``spec`` from pre-bound makers.

    Arity-specialized: 0/1/2 deps call ``make`` directly without a list
    comprehension. Singleton wrappers read/write the live container cache
    with double-checked locking, preserving identity, thread-safety and
    override-visible semantics of :meth:`Container.get`.
    """
    make = spec.make
    assert make is not None

    n = len(dep_makers)
    if n == 0:

        def _inner() -> Any:
            return make()

    elif n == 1:
        (dep0,) = dep_makers

        def _inner() -> Any:
            return make(dep0())

    elif n == 2:
        dep0, dep1 = dep_makers

        def _inner() -> Any:
            return make(dep0(), dep1())

    elif n == 3:
        dep0, dep1, dep2 = dep_makers

        def _inner() -> Any:
            return make(dep0(), dep1(), dep2())

    else:

        def _inner() -> Any:
            return make(*[d() for d in dep_makers])

    if spec.lifetime != "singleton":
        return _inner

    single = container.single
    lock = container.lock
    key = spec.key

    def _maker() -> Any:
        cached = single.get(key, _MISSING)
        if cached is not _MISSING:
            return cached
        value = _inner()
        with lock:
            existing = single.get(key, _MISSING)
            if existing is _MISSING:
                single[key] = value
            else:
                value = existing
        return value

    return _maker


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable, pre-compiled execution plan for a container.

    The plan holds a topological ordering of the registered rules plus the
    resolved dependency edges. ``get`` walks the precomputed order and
    resolves directly without re-entering ``Container.get``, so lifetimes,
    caches and scopes keep the same semantics as ``Container.get``.

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
    node_index: Dict[Key, int] = field(default_factory=dict)
    nodes: Tuple[_NodeSpec, ...] = field(default_factory=tuple)
    resolvers: Dict[Key, Callable[[], Any]] = field(default_factory=dict)

    def _resolve_fast(self, lookup: Key) -> Any:
        """Resolve ``lookup`` using the precomputed node graph.

        Walks nodes in topological order up to and including the requested
        key. Singletons are read from / written to the live container cache so
        overrides and cross-plan identity are preserved. Transients are built
        fresh on every call. No reflection, no locks on the warm path, no
        ``ResolveContext`` allocation.
        """
        container = self.container
        idx = self.node_index.get(lookup)
        if idx is None:
            if container is not None:
                return container.get(lookup)
            idx = self.node_index.get(_key_repr(lookup))
            if idx is None:
                raise ServiceNotFoundError(lookup)

        nodes = self.nodes
        if container is None:
            resolved = [None] * (idx + 1)
            for i in range(idx + 1):
                spec = nodes[i]
                if spec.lifetime == "singleton":
                    cached = self.singletons.get(self.order[i], _MISSING)
                    if cached is _MISSING:
                        cached = self.singletons.get(_key_repr(spec.key), _MISSING)
                    if cached is _MISSING and isinstance(spec.key, str):
                        cached = self.singletons.get(spec.key, _MISSING)
                    if cached is not _MISSING:
                        resolved[i] = cached
                        continue
                make = spec.make
                if make is None:
                    raise ServiceNotFoundError(spec.key)
                deps = spec.deps_idx
                dep_objs = make(*[resolved[j] for j in deps]) if deps else make()
                resolved[i] = dep_objs
            return resolved[idx]

        single = container.single
        lock = container.lock
        override_layers = container._override_layers
        tracer = container._tracer
        started = tracer is not None
        start = 0.0
        if started:
            start = time.perf_counter()

        resolved = [None] * (idx + 1)
        for i in range(idx + 1):
            spec = nodes[i]
            if override_layers:
                overridden = container._resolve_override(spec.key)
                if overridden is not _unset:
                    resolved[i] = overridden
                    continue
            if spec.lifetime == "singleton":
                cached = single.get(spec.key, _MISSING)
                if cached is not _MISSING:
                    resolved[i] = cached
                    continue

            if spec.yield_provider or spec.async_yield_provider or spec.is_async:
                return container.get(spec.key)

            make = spec.make
            if make is None:
                return container.get(spec.key)

            deps = spec.deps_idx
            obj = make(*[resolved[j] for j in deps]) if deps else make()

            if spec.lifetime == "singleton":
                with lock:
                    existing = single.get(spec.key, _MISSING)
                    if existing is _MISSING:
                        single[spec.key] = obj
                    else:
                        obj = existing
            if spec.nested:
                container._cache_nested_aliases(spec.key, obj)
            if started:
                container._trace(spec.key, time.perf_counter() - start, False, None)
            resolved[i] = obj

        return resolved[idx]

    def get(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Resolve ``key`` using the precomputed order.

        Semantics match ``container.get(key)``: singletons share the container
        cache, scopes are honoured, overrides are applied.
        """
        lookup = (key, qualifier) if qualifier is not None else key
        if self.nodes:
            container = self.container
            resolvers = self.resolvers
            if (
                container is not None
                and not container._override_layers
                and container._tracer is None
            ):
                resolver = resolvers.get(lookup)
                if resolver is not None:
                    return resolver()
            return self._resolve_fast(lookup)
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

        for key, rule in ruleset.map.items():
            try:
                sig = inspect.signature(rule.make)
            except (TypeError, ValueError):
                sig = None
            if sig is not None:
                positional = [
                    p
                    for p in sig.parameters.values()
                    if p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                required = sum(1 for p in positional if p.default is inspect.Parameter.empty)
                total = len(positional)
                has_varargs = any(
                    p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
                )
                if len(rule.deps) < required:
                    raise InvalidFactoryError(
                        key,
                        f"factory requires at least {required} args "
                        f"but only {len(rule.deps)} deps declared",
                    ) from None
                if len(rule.deps) > total and not has_varargs:
                    raise InvalidFactoryError(
                        key,
                        f"factory accepts at most {total} args but {len(rule.deps)} deps declared",
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

        # Build fast-path node specs keyed by object key.
        key_to_idx: Dict[Key, int] = {}
        for i, repr_key in enumerate(order):
            key_to_idx[keys[repr_key]] = i

        nodes: List[_NodeSpec] = []
        for repr_key in order:
            key = keys[repr_key]
            rule = rules_map[key]
            deps_idx = tuple(key_to_idx[d] for d in rule.deps if d in key_to_idx)
            nodes.append(
                _NodeSpec(
                    key=key,
                    make=rule.make,
                    deps_idx=deps_idx,
                    lifetime=rule.lifetime,
                    yield_provider=rule.yield_provider,
                    async_yield_provider=rule.async_yield_provider,
                    is_async=rule.is_async,
                    nested=rule.nested,
                )
            )

        makers: List[Optional[Callable[[], Any]]] = [None] * len(nodes)
        resolvers: Dict[Key, Callable[[], Any]] = {}
        for i, spec in enumerate(nodes):
            if (
                spec.make is None
                or spec.yield_provider
                or spec.async_yield_provider
                or spec.is_async
                or spec.nested
            ):
                continue
            if not all(makers[j] is not None for j in spec.deps_idx):
                continue
            dep_makers = tuple(cast(Callable[[], Any], makers[j]) for j in spec.deps_idx)
            maker = _build_node_maker(spec, dep_makers, container)
            makers[i] = maker
            resolvers[spec.key] = maker

        policy = container.config.compile_policy.value
        return cls(
            container=container,
            order=tuple(order),
            edges=edges,
            rules=meta,
            keys=keys,
            singletons={},
            compile_policy=policy,
            node_index=key_to_idx,
            nodes=tuple(nodes),
            resolvers=resolvers,
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
        order = tuple(payload["order"])
        repr_to_idx = {rk: i for i, rk in enumerate(order)}

        key_to_idx: Dict[Key, int] = {}
        for i, rk in enumerate(order):
            if rk not in keys:
                continue
            key = keys[rk]
            key_to_idx[key] = i
            key_to_idx[rk] = i

        nodes: List[_NodeSpec] = []
        for rk in order:
            meta = payload.get("rules", {}).get(rk)
            if meta is None or rk not in keys:
                continue
            key = keys[rk]
            deps: List[Any] = meta.get("deps", [])
            deps_idx = tuple(repr_to_idx[d] for d in deps if d in repr_to_idx)
            nodes.append(
                _NodeSpec(
                    key=key,
                    make=None,
                    deps_idx=deps_idx,
                    lifetime=str(meta.get("lifetime", "transient")),
                    yield_provider=bool(meta.get("yield")),
                    async_yield_provider=bool(meta.get("async")),
                    is_async=bool(meta.get("is_async")),
                    nested=bool(meta.get("nested")),
                )
            )

        return cls(
            container=None,
            order=order,
            edges=payload["edges"],
            rules=payload["rules"],
            keys=keys,
            singletons=singletons,
            compile_policy=payload.get("policy", "allow_override"),
            node_index=key_to_idx,
            nodes=tuple(nodes),
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
