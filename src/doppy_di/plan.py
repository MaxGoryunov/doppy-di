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
    frozen: Optional[Dict[Key, Any]] = None,
) -> Callable[[], Any]:
    """Build a flat closure that resolves ``spec`` from pre-bound makers.

    Arity-specialized: 0/1/2 deps call ``make`` directly without a list
    comprehension. Singleton wrappers read/write the live container cache
    with double-checked locking, preserving identity, thread-safety and
    override-visible semantics of :meth:`Container.get`.

    When ``frozen`` is given, singletons are pre-resolved constants and the
    resolver reads ``frozen[spec.key]`` directly with no lock.
    """
    make = spec.make
    assert make is not None

    if frozen is not None and spec.lifetime == "singleton":
        return lambda: frozen[spec.key]

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

    if frozen is not None:
        return _inner
    return _wrap_singleton(_inner, spec.key, container)


def _wrap_singleton(
    inner: Callable[[], Any],
    key: Key,
    container: Container,
) -> Callable[[], Any]:
    """Wrap ``inner`` with double-checked-locking singleton caching.

    Reads/writes the live container cache so identity, thread-safety and
    override-visible semantics match :meth:`Container.get`.
    """
    single = container.single
    lock = container.lock

    def _maker() -> Any:
        cached = single.get(key, _MISSING)
        if cached is not _MISSING:
            return cached
        value = inner()
        with lock:
            existing = single.get(key, _MISSING)
            if existing is not _MISSING:
                return existing
            single[key] = value
        return value

    return _maker


# --- Issue #40: flattened transient subgraphs --------------------------------

_PreludeFetch = Callable[[], Tuple[Any, ...]]
_ArgExpr = Callable[[Tuple[Any, ...]], Any]
_FlatSlot = Tuple[str, Any]

_MAX_FLAT_NODES = 64
_MAX_FLAT_DEPTH = 32


def _make_prelude_fetch(makers: Tuple[Callable[[], Any], ...]) -> _PreludeFetch:
    """Build a callable evaluating all prelude makers into one fresh tuple.

    Arity-specialized up to four entries so the common case avoids a
    generator expression. Called exactly once per resolution (CSE): shared
    singletons are fetched a single time no matter how many transients
    reference them.
    """
    n = len(makers)
    if n == 0:

        def _fetch0() -> Tuple[Any, ...]:
            return ()

        return _fetch0
    if n == 1:
        m0 = makers[0]

        def _fetch1() -> Tuple[Any, ...]:
            return (m0(),)

        return _fetch1
    if n == 2:
        m0, m1 = makers

        def _fetch2() -> Tuple[Any, ...]:
            return (m0(), m1())

        return _fetch2
    if n == 3:
        m0, m1, m2 = makers

        def _fetch3() -> Tuple[Any, ...]:
            return (m0(), m1(), m2())

        return _fetch3
    if n == 4:
        m0, m1, m2, m3 = makers

        def _fetch4() -> Tuple[Any, ...]:
            return (m0(), m1(), m2(), m3())

        return _fetch4

    def _fetch_n() -> Tuple[Any, ...]:
        return tuple(m() for m in makers)

    return _fetch_n


def _emit_literal_root(
    make_r: Callable[..., Any],
    pre: _PreludeFetch,
    slots: Tuple[_FlatSlot, ...],
) -> Callable[[], Any]:
    """Build a root closure with zero intermediate DI frames.

    Each slot is ``("p", j)`` — pass prelude item ``j`` straight through —
    or ``("l1", (mk, j))`` — call a one-dependency factory directly over
    prelude item ``j``. Transient constructor calls appear literally in the
    root body, mirroring hand-written wiring.
    """
    if len(slots) == 1:
        kind0, payload0 = slots[0]
        if kind0 == "p":
            d0 = cast(int, payload0)

            def _lit_p1() -> Any:
                p = pre()
                return make_r(p[d0])

            return _lit_p1
        mk0, d0 = cast(Tuple[Callable[[Any], Any], int], payload0)

        def _lit_l11() -> Any:
            p = pre()
            return make_r(mk0(p[d0]))

        return _lit_l11
    if len(slots) == 2:
        kind0, payload0 = slots[0]
        kind1, payload1 = slots[1]
        if kind0 == "p" and kind1 == "p":
            d0 = cast(int, payload0)
            d1 = cast(int, payload1)

            def _lit_pp() -> Any:
                p = pre()
                return make_r(p[d0], p[d1])

            return _lit_pp
        if kind0 == "p":
            d0 = cast(int, payload0)
            mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)

            def _lit_pl() -> Any:
                p = pre()
                return make_r(p[d0], mk1(p[d1]))

            return _lit_pl
        mk0, d0 = cast(Tuple[Callable[[Any], Any], int], payload0)
        if kind1 == "p":
            d1 = cast(int, payload1)

            def _lit_lp() -> Any:
                p = pre()
                return make_r(mk0(p[d0]), p[d1])

            return _lit_lp
        mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)

        def _lit_ll() -> Any:
            p = pre()
            return make_r(mk0(p[d0]), mk1(p[d1]))

        return _lit_ll
    kind0, payload0 = slots[0]
    kind1, payload1 = slots[1]
    kind2, payload2 = slots[2]
    if kind0 == "p" and kind1 == "p" and kind2 == "p":
        d0 = cast(int, payload0)
        d1 = cast(int, payload1)
        d2 = cast(int, payload2)

        def _lit_ppp() -> Any:
            p = pre()
            return make_r(p[d0], p[d1], p[d2])

        return _lit_ppp
    if kind0 == "p" and kind1 == "p":
        d0 = cast(int, payload0)
        d1 = cast(int, payload1)
        mk2, d2 = cast(Tuple[Callable[[Any], Any], int], payload2)

        def _lit_ppl() -> Any:
            p = pre()
            return make_r(p[d0], p[d1], mk2(p[d2]))

        return _lit_ppl
    if kind0 == "p" and kind2 == "p":
        d0 = cast(int, payload0)
        mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)
        d2 = cast(int, payload2)

        def _lit_plp() -> Any:
            p = pre()
            return make_r(p[d0], mk1(p[d1]), p[d2])

        return _lit_plp
    if kind0 == "p":
        d0 = cast(int, payload0)
        mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)
        mk2, d2 = cast(Tuple[Callable[[Any], Any], int], payload2)

        def _lit_pll() -> Any:
            p = pre()
            return make_r(p[d0], mk1(p[d1]), mk2(p[d2]))

        return _lit_pll
    mk0, d0 = cast(Tuple[Callable[[Any], Any], int], payload0)
    if kind1 == "p" and kind2 == "p":
        d1 = cast(int, payload1)
        d2 = cast(int, payload2)

        def _lit_lpp() -> Any:
            p = pre()
            return make_r(mk0(p[d0]), p[d1], p[d2])

        return _lit_lpp
    if kind1 == "p":
        d1 = cast(int, payload1)
        mk2, d2 = cast(Tuple[Callable[[Any], Any], int], payload2)

        def _lit_lpl() -> Any:
            p = pre()
            return make_r(mk0(p[d0]), p[d1], mk2(p[d2]))

        return _lit_lpl
    if kind2 == "p":
        mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)
        d2 = cast(int, payload2)

        def _lit_llp() -> Any:
            p = pre()
            return make_r(mk0(p[d0]), mk1(p[d1]), p[d2])

        return _lit_llp
    mk1, d1 = cast(Tuple[Callable[[Any], Any], int], payload1)
    mk2, d2 = cast(Tuple[Callable[[Any], Any], int], payload2)

    def _lit_lll() -> Any:
        p = pre()
        return make_r(mk0(p[d0]), mk1(p[d1]), mk2(p[d2]))

    return _lit_lll


def _emit_ref(j: int) -> _ArgExpr:
    """Build an argument expression reading prelude item ``j``."""

    def _ref(p: Tuple[Any, ...]) -> Any:
        return p[j]

    return _ref


def _emit_expr(make: Callable[..., Any], args: Tuple[_ArgExpr, ...]) -> _ArgExpr:
    """Build an expression closure invoking ``make`` over arg expressions."""
    k = len(args)
    if k == 0:

        def _x0(p: Tuple[Any, ...]) -> Any:
            return make()

        return _x0
    if k == 1:
        a0 = args[0]

        def _x1(p: Tuple[Any, ...]) -> Any:
            return make(a0(p))

        return _x1
    if k == 2:
        a0, a1 = args

        def _x2(p: Tuple[Any, ...]) -> Any:
            return make(a0(p), a1(p))

        return _x2
    if k == 3:
        a0, a1, a2 = args

        def _x3(p: Tuple[Any, ...]) -> Any:
            return make(a0(p), a1(p), a2(p))

        return _x3

    def _xn(p: Tuple[Any, ...]) -> Any:
        return make(*[a(p) for a in args])

    return _xn


def _emit_generic_root(
    make_r: Callable[..., Any],
    pre: _PreludeFetch,
    args: Tuple[_ArgExpr, ...],
) -> Callable[[], Any]:
    """Build a root closure evaluating the prelude once, then arg exprs."""
    k = len(args)
    if k == 0:

        def _g0() -> Any:
            return make_r()

        return _g0
    if k == 1:
        a0 = args[0]

        def _g1() -> Any:
            p = pre()
            return make_r(a0(p))

        return _g1
    if k == 2:
        a0, a1 = args

        def _g2() -> Any:
            p = pre()
            return make_r(a0(p), a1(p))

        return _g2
    if k == 3:
        a0, a1, a2 = args

        def _g3() -> Any:
            p = pre()
            return make_r(a0(p), a1(p), a2(p))

        return _g3

    def _gn() -> Any:
        p = pre()
        return make_r(*[a(p) for a in args])

    return _gn


def _flat_node_eligible(spec: _NodeSpec) -> bool:
    """Return True when ``spec`` may participate in a flattened resolver."""
    return (
        spec.make is not None
        and not spec.yield_provider
        and not spec.async_yield_provider
        and not spec.is_async
        and not spec.nested
        and spec.lifetime in ("transient", "singleton")
    )


def _build_flat_resolver(
    root_idx: int,
    nodes: Tuple[_NodeSpec, ...],
    makers: List[Optional[Callable[[], Any]]],
    container: Container,
    frozen: Optional[Dict[Key, Any]] = None,
) -> Optional[Tuple[str, Callable[[], Any]]]:
    """Try to build a flattened resolver for ``nodes[root_idx]``.

    Returns ``(kind, resolver)`` where kind is ``"flat"`` (zero intermediate
    DI frames: prelude CSE plus literal factory calls) or ``"generic"``
    (prelude CSE plus per-node expression closures), or ``None`` when the
    subtree is ineligible and the composed resolver must be kept.
    """
    root = nodes[root_idx]
    if not _flat_node_eligible(root):
        return None

    # Collect the dependency subtree iteratively.
    seen: set[int] = set()
    order: List[int] = []
    stack: List[Tuple[int, int]] = [(root_idx, 0)]
    while stack:
        idx, depth = stack.pop()
        if idx in seen:
            continue
        if depth > _MAX_FLAT_DEPTH or len(order) >= _MAX_FLAT_NODES:
            return None
        spec = nodes[idx]
        if not _flat_node_eligible(spec):
            return None
        seen.add(idx)
        order.append(idx)
        for dep in spec.deps_idx:
            stack.append((dep, depth + 1))

    # Prelude: subtree singletons in topo order, each with a ready wrapper.
    prelude_idx = sorted(i for i in order if nodes[i].lifetime == "singleton")
    for i in prelude_idx:
        if makers[i] is None:
            return None
    pre = _make_prelude_fetch(tuple(cast(Callable[[], Any], makers[i]) for i in prelude_idx))
    prelude_pos = {i: j for j, i in enumerate(prelude_idx)}

    transient_idx = [i for i in order if i != root_idx and nodes[i].lifetime == "transient"]
    leaf_only = all(all(d in prelude_pos for d in nodes[i].deps_idx) for i in transient_idx)

    make_r = cast(Callable[..., Any], root.make)

    if (
        leaf_only
        and 0 < len(root.deps_idx) <= 3
        and all(len(nodes[i].deps_idx) == 1 for i in transient_idx)
    ):
        slots: List[_FlatSlot] = []
        for d in root.deps_idx:
            if d in prelude_pos:
                slots.append(("p", prelude_pos[d]))
            else:
                leaf = nodes[d]
                slots.append(
                    (
                        "l1",
                        (
                            cast(Callable[[Any], Any], leaf.make),
                            prelude_pos[leaf.deps_idx[0]],
                        ),
                    )
                )
        inner = _emit_literal_root(make_r, pre, tuple(slots))
        kind = "flat"
    else:
        exprs: Dict[int, _ArgExpr] = {}

        def _arg_for(dep: int) -> _ArgExpr:
            if dep in prelude_pos:
                return _emit_ref(prelude_pos[dep])
            return exprs[dep]

        for i in sorted(transient_idx):
            spec = nodes[i]
            exprs[i] = _emit_expr(
                cast(Callable[..., Any], spec.make),
                tuple(_arg_for(d) for d in spec.deps_idx),
            )
        inner = _emit_generic_root(make_r, pre, tuple(_arg_for(d) for d in root.deps_idx))
        kind = "generic"

    if root.lifetime == "singleton":
        if frozen is not None:
            return kind, lambda: frozen[root.key]
        return kind, _wrap_singleton(inner, root.key, container)
    return kind, inner


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
    resolver_kinds: Dict[Key, str] = field(default_factory=dict)
    _frozen: Dict[Key, Any] = field(default_factory=dict)
    frozen: bool = False

    def _resolve_fast(self, lookup: Key) -> Any:
        """Resolve ``lookup`` using the precomputed node graph.

        Walks nodes in topological order up to and including the requested
        key. Singletons are read from / written to the live container cache so
        overrides and cross-plan identity are preserved. Transients are built
        fresh on every call. No reflection, no locks on the warm path, no
        ``ResolveContext`` allocation.

        When the plan is frozen, singletons are pre-resolved constants read
        from ``_frozen`` with no lock and no override check.
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
            if self.frozen:
                if spec.lifetime == "singleton":
                    resolved[i] = self._frozen[spec.key]
                    continue
            else:
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

            if not self.frozen and spec.lifetime == "singleton":
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
        cache, scopes are honoured, overrides are applied. When the plan is
        frozen, overrides are disallowed and singletons are pre-resolved.
        """
        lookup = (key, qualifier) if qualifier is not None else key
        if self.nodes:
            container = self.container
            resolvers = self.resolvers
            if container is not None and (
                self.frozen or (not container._override_layers and container._tracer is None)
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
        cls,
        container: Container,
        copy_parent_rules: bool = True,
        allow_post_compile_overrides: bool = True,
    ) -> "ExecutionPlan":
        """Build an :class:`ExecutionPlan` from a container.

        Performs full static validation (missing dependencies, cycles) and
        captures a topological ordering from a snapshot of the rule set.
        Raises :class:`MissingDependencyError` for unregistered dependencies
        and :class:`DependencyCycleError` for cycles.

        When ``allow_post_compile_overrides`` is False, the plan freezes the
        graph at compile time: singletons are pre-resolved into
        ``_frozen`` and the plan uses lockless resolvers. Any later
        ``override(...)`` on the container raises ``RuntimeError``. This is
        a breaking behavioral change for callers relying on override
        visibility through a compiled plan.
        """
        if not allow_post_compile_overrides and container._override_layers:
            raise RuntimeError(
                "Cannot compile with allow_post_compile_overrides=False "
                "while an override layer is active"
            )
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

        frozen: Optional[Dict[Key, Any]] = None
        if not allow_post_compile_overrides:
            frozen = {}
            for _i, spec in enumerate(nodes):
                if spec.lifetime != "singleton":
                    continue
                if spec.make is None:
                    continue
                deps = spec.deps_idx
                args = [frozen[nodes[j].key] for j in deps]
                frozen[spec.key] = spec.make(*args) if args else spec.make()
            object.__setattr__(container, "_compiled_plan", None)

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
            maker = _build_node_maker(spec, dep_makers, container, frozen)
            makers[i] = maker
            resolvers[spec.key] = maker

        # Issue #1: overwrite the same resolvers with flattened ones wherever
        # the dependency subtree qualifies. Falls back silently otherwise.
        nodes_tuple = tuple(nodes)
        resolver_kinds: Dict[Key, str] = dict.fromkeys(resolvers, "composed")
        for i, spec in enumerate(nodes):
            if spec.key not in resolvers:
                continue
            if frozen is not None and spec.lifetime == "singleton":
                resolver_kinds[spec.key] = "frozen"
                continue
            flat = _build_flat_resolver(i, nodes_tuple, makers, container, frozen)
            if flat is None:
                continue
            kind, fn = flat
            resolvers[spec.key] = fn
            resolver_kinds[spec.key] = kind

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
            nodes=nodes_tuple,
            resolvers=resolvers,
            resolver_kinds=resolver_kinds,
            _frozen=frozen or {},
            frozen=frozen is not None,
        )

    def _singleton_snapshot(self) -> Dict[str, Any]:
        """Capture resolved singletons (+ unresolved singleton constants)."""
        container = self.container
        if container is None:
            return dict(self.singletons)
        snapshot: Dict[str, Any] = {_key_repr(k): v for k, v in container.single.items()}
        if self.frozen:
            snapshot.update({_key_repr(k): v for k, v in self._frozen.items()})
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
            "frozen": self.frozen,
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

        frozen = bool(payload.get("frozen", False))
        frozen_map: Dict[Key, Any] = {}
        if frozen:
            for rk, v in payload.get("singletons", {}).items():
                if rk in keys:
                    frozen_map[keys[rk]] = _value_from_serializable(v)
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
            _frozen=frozen_map,
            frozen=frozen,
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
