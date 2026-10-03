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
    _factory_arity,
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
    cell = [_MISSING]

    def _maker() -> Any:
        value = cell[0]
        if value is not _MISSING:
            return value
        cached = single.get(key, _MISSING)
        if cached is not _MISSING:
            cell[0] = cached
            return cached
        value = inner()
        with lock:
            existing = single.get(key, _MISSING)
            if existing is not _MISSING:
                value = existing
            else:
                single[key] = value
        cell[0] = value
        return value

    return _maker


# --- Issue #40: flattened transient subgraphs --------------------------------

_PreludeFetch = Callable[[], Tuple[Any, ...]]
_ArgExpr = Callable[[Tuple[Any, ...]], Any]
_FlatSlot = Tuple[str, Any]
_NoArgExpr = Callable[[], Any]

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
    """Build a root closure with zero intermediate DI frames."""
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

    return _emit_lit3(make_r, pre, slots)


def _emit_lit3(
    make_r: Callable[..., Any],
    pre: _PreludeFetch,
    slots: Tuple[_FlatSlot, ...],
) -> Callable[[], Any]:
    """Three-slot root: all value-ref/leaf combinations."""

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


_Leaf2Slot = Tuple[str, Any]  # ("p", idx) | ("l2", (make, i0, i1))


def _emit_literal2_root(
    make_r: Callable[..., Any],
    pre: _PreludeFetch,
    slots: Tuple[_Leaf2Slot, ...],
) -> Callable[[], Any]:
    """Root closure with 2-value leaf children inlined in a single frame.

    Covers the common shape of a transient root whose transient children each
    pull two values from the singleton prelude. Single frame, zero nested DI
    closures on the hot path.
    """
    if len(slots) == 2:
        kind0, payload0 = slots[0]
        kind1, payload1 = slots[1]
        if kind0 == "p":
            d0 = cast(int, payload0)
            if kind1 == "p":
                d1 = cast(int, payload1)

                def _a_pp() -> Any:
                    p = pre()
                    return make_r(p[d0], p[d1])

                return _a_pp
            mk1, e1, f1 = cast(Tuple[Callable[..., Any], int, int], payload1)

            def _a_pl() -> Any:
                p = pre()
                return make_r(p[d0], mk1(p[e1], p[f1]))

            return _a_pl
        mk0, e0, f0 = cast(Tuple[Callable[..., Any], int, int], payload0)
        if kind1 == "p":
            d1 = cast(int, payload1)

            def _a_lp() -> Any:
                p = pre()
                return make_r(mk0(p[e0], p[f0]), p[d1])

            return _a_lp
        mk1, e1, f1 = cast(Tuple[Callable[..., Any], int, int], payload1)

        def _a_ll() -> Any:
            p = pre()
            return make_r(mk0(p[e0], p[f0]), mk1(p[e1], p[f1]))

        return _a_ll

    return _emit_lit2_3(make_r, pre, slots)


def _emit_lit2_3(
    make_r: Callable[..., Any],
    pre: _PreludeFetch,
    slots: Tuple[_Leaf2Slot, ...],
) -> Callable[[], Any]:
    """Three two-value leaves inlined in a single frame."""

    kind0, payload0 = slots[0]
    kind1, payload1 = slots[1]
    kind2, payload2 = slots[2]
    if kind0 == "p":
        d0 = cast(int, payload0)
        if kind1 == "p":
            d1 = cast(int, payload1)
            if kind2 == "p":
                d2 = cast(int, payload2)

                def _a_ppp() -> Any:
                    p = pre()
                    return make_r(p[d0], p[d1], p[d2])

                return _a_ppp
            mk2, e2, f2 = cast(Tuple[Callable[..., Any], int, int], payload2)

            def _a_ppl() -> Any:
                p = pre()
                return make_r(p[d0], p[d1], mk2(p[e2], p[f2]))

            return _a_ppl
        if kind2 == "p":
            d2 = cast(int, payload2)
            mk1, e1, f1 = cast(Tuple[Callable[..., Any], int, int], payload1)

            def _a_plp() -> Any:
                p = pre()
                return make_r(p[d0], mk1(p[e1], p[f1]), p[d2])

            return _a_plp
        mk1, e1, f1 = cast(Tuple[Callable[..., Any], int, int], payload1)
        mk2, e2, f2 = cast(Tuple[Callable[..., Any], int, int], payload2)

        def _a_pll() -> Any:
            p = pre()
            return make_r(p[d0], mk1(p[e1], p[f1]), mk2(p[e2], p[f2]))

        return _a_pll
    mk0, e0, f0 = cast(Tuple[Callable[..., Any], int, int], payload0)
    if kind1 == "p":
        d1 = cast(int, payload1)
        if kind2 == "p":
            d2 = cast(int, payload2)

            def _a_lpp() -> Any:
                p = pre()
                return make_r(mk0(p[e0], p[f0]), p[d1], p[d2])

            return _a_lpp
        mk2, e2, f2 = cast(Tuple[Callable[..., Any], int, int], payload2)

        def _a_lpl() -> Any:
            p = pre()
            return make_r(mk0(p[e0], p[f0]), p[d1], mk2(p[e2], p[f2]))

        return _a_lpl
    mk1, e1, f1 = cast(Tuple[Callable[..., Any], int, int], payload1)
    if kind2 == "p":
        d2 = cast(int, payload2)

        def _a_llp() -> Any:
            p = pre()
            return make_r(mk0(p[e0], p[f0]), mk1(p[e1], p[f1]), p[d2])

        return _a_llp
    mk2, e2, f2 = cast(Tuple[Callable[..., Any], int, int], payload2)

    def _a_lll() -> Any:
        p = pre()
        return make_r(mk0(p[e0], p[f0]), mk1(p[e1], p[f1]), mk2(p[e2], p[f2]))

    return _a_lll


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
    """Try to build a flattened resolver for ``nodes[root_idx]``."""
    root = nodes[root_idx]
    if not _flat_node_eligible(root):
        return None

    order = _collect_eligible_order(nodes, root_idx, _flat_node_eligible)
    if order is None:
        return None

    prelude = _flat_prelude(nodes, order, makers)
    if prelude is None:
        return None
    pre, prelude_pos = prelude

    transient_idx = [i for i in order if i != root_idx and nodes[i].lifetime == "transient"]
    kind, inner = _flatten_root(root, nodes, pre, prelude_pos, transient_idx)
    if root.lifetime == "singleton":
        if frozen is not None:
            return kind, lambda: frozen[root.key]
        return kind, _wrap_singleton(inner, root.key, container)
    return kind, inner


def _collect_eligible_order(
    nodes: Tuple[_NodeSpec, ...],
    root_idx: int,
    eligible: Callable[[_NodeSpec], bool],
) -> Optional[List[int]]:
    """DFS the eligible sub-graph rooted at ``root_idx``.

    Returns ``None`` when a node is ineligible or the sub-graph exceeds the
    flat-resolver depth/node caps.
    """
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
        if not eligible(spec):
            return None
        seen.add(idx)
        order.append(idx)
        for dep in spec.deps_idx:
            stack.append((dep, depth + 1))
    return order


def _flat_prelude(
    nodes: Tuple[_NodeSpec, ...],
    order: List[int],
    makers: List[Optional[Callable[[], Any]]],
) -> Optional[Tuple[_PreludeFetch, Dict[int, int]]]:
    """Build the singleton fetch closure and its node-index mapping."""
    prelude_idx = sorted(i for i in order if nodes[i].lifetime == "singleton")
    if any(makers[i] is None for i in prelude_idx):
        return None
    pre = _make_prelude_fetch(tuple(cast(Callable[[], Any], makers[i]) for i in prelude_idx))
    return pre, {i: j for j, i in enumerate(prelude_idx)}


def _flatten_root(
    root: _NodeSpec,
    nodes: Tuple[_NodeSpec, ...],
    pre: _PreludeFetch,
    prelude_pos: Dict[int, int],
    transient_idx: List[int],
) -> Tuple[str, Callable[[], Any]]:
    """Return the flattened resolver kind and its root closure."""
    leaf_only = all(all(d in prelude_pos for d in nodes[i].deps_idx) for i in transient_idx)
    make_r = cast(Callable[..., Any], root.make)
    leaf_arities = {len(nodes[i].deps_idx) for i in transient_idx}
    if (
        leaf_only
        and 0 < len(root.deps_idx) <= 3
        and (
            (leaf_arities == set() or leaf_arities == {1})
            or (leaf_arities == {2} and len(root.deps_idx) >= 2)
        )
    ):
        return _flatten_literal_root(root, nodes, pre, prelude_pos, leaf_arities)

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
    return "generic", inner


def _flatten_literal_root(
    root: _NodeSpec,
    nodes: Tuple[_NodeSpec, ...],
    pre: _PreludeFetch,
    prelude_pos: Dict[int, int],
    leaf_arities: set[int],
) -> Tuple[str, Callable[[], Any]]:
    """Inline a root whose transient children only read the prelude."""
    slots = _flat_leaf_slots(root, nodes, prelude_pos)
    make_r = cast(Callable[..., Any], root.make)
    if leaf_arities == {2}:
        return "flat", _emit_literal2_root(make_r, pre, tuple(slots))
    return "flat", _emit_literal_root(make_r, pre, tuple(slots))


def _flat_leaf_slots(
    root: _NodeSpec,
    nodes: Tuple[_NodeSpec, ...],
    prelude_pos: Dict[int, int],
) -> List[_FlatSlot]:
    """Build literal slots for a root whose children only read the prelude."""
    slots: List[_FlatSlot] = []
    for d in root.deps_idx:
        if d in prelude_pos:
            slots.append(("p", prelude_pos[d]))
            continue
        leaf = nodes[d]
        leaf_deps = leaf.deps_idx
        if len(leaf_deps) == 1:
            slots.append(
                (
                    "l1",
                    (
                        cast(Callable[[Any], Any], leaf.make),
                        prelude_pos[leaf_deps[0]],
                    ),
                )
            )
        else:
            slots.append(
                (
                    "l2",
                    (
                        cast(Callable[..., Any], leaf.make),
                        prelude_pos[leaf_deps[0]],
                        prelude_pos[leaf_deps[1]],
                    ),
                )
            )
    return slots


# --- Issue #43: exec-free frozen fast path ------------------------------------


def _has_shared_transient(nodes: Tuple[_NodeSpec, ...], order: List[int]) -> bool:
    """Return True when a transient node in ``order`` has several parents."""
    parents: Dict[int, int] = dict.fromkeys(order, 0)
    for i in order:
        for d in nodes[i].deps_idx:
            if d in parents:
                parents[d] += 1
    return any(nodes[i].lifetime == "transient" and parents[i] > 1 for i in order)


def _frozen_node_eligible(spec: _NodeSpec) -> bool:
    """Return True when ``spec`` may run in a lockless frozen resolver."""
    return (
        spec.make is not None
        and not spec.yield_provider
        and not spec.async_yield_provider
        and not spec.is_async
        and not spec.nested
        and spec.lifetime in ("transient", "singleton")
    )


def _emit_const(value: Any) -> _NoArgExpr:
    """Capture an immutable frozen singleton as a no-arg callable."""

    def _const(_v: Any = value) -> Any:
        return _v

    return _const


def _emit_make(
    make: Callable[..., Any],
    dep_exprs: Tuple[_NoArgExpr, ...],
) -> _NoArgExpr:
    """No-arg arity-specialized closure calling ``make`` over dep exprs."""
    k = len(dep_exprs)
    if k == 0:

        def _m0(_f: Callable[..., Any] = make) -> Any:
            return _f()

        return _m0
    if k == 1:
        d0 = dep_exprs[0]

        def _m1(_f: Callable[..., Any] = make, _d0: _NoArgExpr = d0) -> Any:
            return _f(_d0())

        return _m1
    if k == 2:
        d0, d1 = dep_exprs

        def _m2(
            _f: Callable[..., Any] = make,
            _d0: _NoArgExpr = d0,
            _d1: _NoArgExpr = d1,
        ) -> Any:
            return _f(_d0(), _d1())

        return _m2
    if k == 3:
        d0, d1, d2 = dep_exprs

        def _m3(
            _f: Callable[..., Any] = make,
            _d0: _NoArgExpr = d0,
            _d1: _NoArgExpr = d1,
            _d2: _NoArgExpr = d2,
        ) -> Any:
            return _f(_d0(), _d1(), _d2())

        return _m3
    deps = dep_exprs

    def _mn(_f: Callable[..., Any] = make, _d: Tuple[_NoArgExpr, ...] = deps) -> Any:
        return _f(*[x() for x in _d])

    return _mn


def _build_frozen_resolver(
    root_idx: int,
    nodes: Tuple[_NodeSpec, ...],
    frozen: Dict[Key, Any],
) -> Optional[Tuple[str, Callable[[], Any]]]:
    """Build a lockless frozen resolver for an eligible subtree.

    Captures frozen singleton constants directly in closure cells (no
    prelude, no singleton dict lookup, no lock, no override/scope checks).
    Inlines eligible transient sub-graphs. Returns ``None`` when the
    subtree is ineligible or contains shared transients (which require a
    single-evaluation slot).
    """
    root = nodes[root_idx]
    if not _frozen_node_eligible(root):
        return None

    order = _collect_eligible_order(nodes, root_idx, _frozen_node_eligible)
    if order is None:
        return None

    if _has_shared_transient(nodes, order):
        return None

    exprs: Dict[int, _NoArgExpr] = {}

    def expr_for(idx: int) -> _NoArgExpr:
        if idx in exprs:
            return exprs[idx]
        spec = nodes[idx]
        if spec.lifetime == "singleton":
            e = _emit_const(frozen.get(spec.key, _MISSING))
        else:
            e = _emit_make(
                cast(Callable[..., Any], spec.make),
                tuple(expr_for(d) for d in spec.deps_idx),
            )
        exprs[idx] = e
        return e

    root_expr = expr_for(root_idx)
    if root.lifetime == "singleton":
        return "frozen", root_expr
    return "flat", root_expr


@dataclass(frozen=True, slots=True)
class BoundResolver:
    """A resolver bound to a single root key.

    Avoids repeated root-key dictionary lookup in :meth:`ExecutionPlan.get`.
    The resolver caches the chosen execution mode at bind time and exposes
    it through :attr:`kind`.

    On the hot path ``__call__`` invokes the stored resolver callable
    directly with zero dict lookups. A dynamic guard (overrides active,
    tracer attached) is re-checked on each call for the mutable plan mode so
    that override/tracer semantics stay identical to :meth:`ExecutionPlan.get`.
    """

    plan: "ExecutionPlan"
    key: Key
    qualifier: Optional[str]
    _direct: Optional[Callable[[], Any]] = None
    _needs_guard: bool = False

    def __call__(self) -> Any:
        direct = self._direct
        if direct is not None:
            if not self._needs_guard:
                return direct()
            container = self.plan.container
            if (
                container is not None
                and not container._override_layers
                and container._tracer is None
            ):
                return direct()
        return self.plan.get(self.key, self.qualifier)

    @property
    def kind(self) -> str:
        lookup = (self.key, self.qualifier) if self.qualifier is not None else self.key
        return self.plan.resolver_kinds.get(lookup, "fallback")


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
    guardless: bool = False

    def bind(self, key: Key, qualifier: Optional[str] = None) -> BoundResolver:
        """Return a bound resolver for ``key`` without repeated root lookup.

        The returned callable validates the key at bind time and preserves
        the same semantics as :meth:`get`. It exposes the selected
        execution mode through :attr:`BoundResolver.kind`.
        """
        lookup = (key, qualifier) if qualifier is not None else key
        self._ensure_bound(key, qualifier, lookup)
        resolver = self.resolvers.get(lookup)
        needs_guard = False
        direct: Optional[Callable[[], Any]] = None
        if resolver is not None:
            if self.guardless:
                direct = resolver
            else:
                container = self.container
                if container is not None and (
                    self.frozen or (not container._override_layers and container._tracer is None)
                ):
                    needs_guard = not self.frozen
                    direct = resolver
        return BoundResolver(
            plan=self,
            key=key,
            qualifier=qualifier,
            _direct=direct,
            _needs_guard=needs_guard,
        )

    def _ensure_bound(self, key: Key, qualifier: Optional[str], lookup: Key) -> None:
        """Raise :class:`ServiceNotFoundError` when ``lookup`` cannot resolve."""
        known = lookup in self.node_index or lookup in self.resolver_kinds
        if known:
            return
        if self.nodes:
            if self.container is not None and not self.container.has(key, qualifier):
                raise ServiceNotFoundError(key)
            return
        if self.container is None and _key_repr(lookup) not in self.singletons:
            raise ServiceNotFoundError(key)

    def _resolve_fast(self, lookup: Key) -> Any:
        """Resolve ``lookup`` using the precomputed node graph."""
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
            return self._resolve_static(idx, nodes)
        return self._resolve_live(idx, nodes, container)

    def _resolve_static(self, idx: int, nodes: Tuple[_NodeSpec, ...]) -> Any:
        """Resolve up to ``idx`` without a live container (recorded singletons)."""
        resolved: List[Any] = [None] * (idx + 1)
        for i in range(idx + 1):
            spec = nodes[i]
            cached = self._static_singleton(i, spec)
            if cached is not _MISSING:
                resolved[i] = cached
                continue
            make = spec.make
            if make is None:
                raise ServiceNotFoundError(spec.key)
            deps = spec.deps_idx
            resolved[i] = make(*[resolved[j] for j in deps]) if deps else make()
        return resolved[idx]

    def _static_singleton(self, i: int, spec: _NodeSpec) -> Any:
        """Return the recorded singleton for node ``i``, else ``_MISSING``."""
        if spec.lifetime != "singleton":
            return _MISSING
        cached = self.singletons.get(self.order[i], _MISSING)
        if cached is _MISSING:
            cached = self.singletons.get(_key_repr(spec.key), _MISSING)
        if cached is _MISSING and isinstance(spec.key, str):
            cached = self.singletons.get(spec.key, _MISSING)
        return cached

    def _resolve_live(
        self,
        idx: int,
        nodes: Tuple[_NodeSpec, ...],
        container: Container,
    ) -> Any:
        """Resolve up to ``idx`` against a live container (caches and locks)."""
        single = container.single
        lock = container.lock
        override_layers = container._override_layers
        started = container._tracer is not None
        start = time.perf_counter() if started else 0.0

        resolved: List[Any] = [None] * (idx + 1)
        for i in range(idx + 1):
            spec = nodes[i]
            is_cached, cached = self._cached_node(spec, container, single, override_layers)
            if is_cached:
                resolved[i] = cached
                continue
            if self._requires_container(spec):
                return container.get(spec.key)
            make = cast(Callable[..., Any], spec.make)
            deps = spec.deps_idx
            obj = make(*[resolved[j] for j in deps]) if deps else make()
            resolved[i] = self._finish_node(spec, obj, container, single, lock, started, start)
        return resolved[idx]

    def _cached_node(
        self,
        spec: _NodeSpec,
        container: Container,
        single: Dict[Key, Any],
        override_layers: List[Any],
    ) -> Tuple[bool, Any]:
        """Return ``(True, value)`` when the node is already available."""
        if self.frozen:
            if spec.lifetime != "singleton":
                return False, None
            return True, self._frozen[spec.key]
        if override_layers:
            overridden = container._resolve_override(spec.key)
            if overridden is not _unset:
                return True, overridden
        if spec.lifetime == "singleton":
            cached = single.get(spec.key, _MISSING)
            if cached is not _MISSING:
                return True, cached
        return False, None

    def _requires_container(self, spec: _NodeSpec) -> bool:
        """Return True when the node cannot be built inline by the fast path."""
        return (
            spec.make is None or spec.yield_provider or spec.async_yield_provider or spec.is_async
        )

    def _finish_node(
        self,
        spec: _NodeSpec,
        obj: Any,
        container: Container,
        single: Dict[Key, Any],
        lock: Any,
        started: bool,
        start: float,
    ) -> Any:
        """Store singletons, cache nested aliases, record tracing."""
        obj = self._store_singleton(spec, obj, single, lock)
        if spec.nested:
            container._cache_nested_aliases(spec.key, obj)
        if started:
            container._trace(spec.key, time.perf_counter() - start, False, None)
        return obj

    def _store_singleton(
        self,
        spec: _NodeSpec,
        obj: Any,
        single: Dict[Key, Any],
        lock: Any,
    ) -> Any:
        """Persist a new singleton under the lock and return the winning value."""
        if self.frozen or spec.lifetime != "singleton":
            return obj
        with lock:
            existing = single.get(spec.key, _MISSING)
            if existing is not _MISSING:
                return existing
            single[spec.key] = obj
            return obj

    def get(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Resolve ``key`` using the precomputed order."""
        lookup = (key, qualifier) if qualifier is not None else key
        if not self.nodes:
            return self._get_uncached(lookup)
        if self.guardless:
            resolver = self.resolvers.get(lookup)
            if resolver is not None:
                return resolver()
            return self._resolve_fast(lookup)
        container = self.container
        if container is not None and (
            self.frozen or (not container._override_layers and container._tracer is None)
        ):
            resolver = self.resolvers.get(lookup)
            if resolver is not None:
                return resolver()
        return self._resolve_fast(lookup)

    def _get_uncached(self, lookup: Key) -> Any:
        """Resolve without a compiled node graph (container or frozen values)."""
        container = self.container
        if container is not None:
            return container.get(lookup)
        lookup_repr = _key_repr(lookup)
        if lookup_repr in self.singletons:
            return self.singletons[lookup_repr]
        raise ServiceNotFoundError(lookup)

    def aget(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Async resolution using the precomputed order."""
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
        guardless: bool = False,
    ) -> "ExecutionPlan":
        """Build an :class:`ExecutionPlan` from a container."""
        if not allow_post_compile_overrides and container._override_layers:
            raise RuntimeError(
                "Cannot compile with allow_post_compile_overrides=False "
                "while an override layer is active"
            )
        ruleset = container.config.ruleset
        _add_implicit_collections(ruleset)
        _check_missing_dependencies(ruleset)

        for key, rule in ruleset.map.items():
            _check_factory_arity(key, rule)

        _check_cycles(ruleset)

        if copy_parent_rules and isinstance(ruleset, CompositeRuleSet):
            rules_map: Dict[Key, Rule] = dict(ruleset.map)
        else:
            rules_map = ruleset.map

        order, edges = _topological_order(ruleset, rules_map)
        meta, keys = _rule_meta_map(rules_map)
        key_to_idx, nodes = _plan_nodes(order, keys, rules_map)

        frozen = _freeze_singletons(container, nodes, allow_post_compile_overrides, guardless)

        makers, resolvers = _node_makers(nodes, container, frozen)

        nodes_tuple = tuple(nodes)
        resolver_kinds = _refine_resolvers(nodes, resolvers, makers, container, frozen, nodes_tuple)

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
            guardless=guardless,
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
        """Serialize the plan to a string for caching or cross-process use."""
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


def _add_implicit_collections(ruleset: RuleSetProtocol) -> None:
    """Register implicit collection rules for unregistered dependency keys."""
    from .providers import implicit_collection_rule

    for _key, rule in list(ruleset.map.items()):
        for dep in rule.deps:
            if dep in ruleset.map:
                continue
            collection = implicit_collection_rule(dep, ruleset)
            if collection is not None:
                ruleset.add(dep, collection)


def _check_missing_dependencies(ruleset: RuleSetProtocol) -> None:
    """Raise :class:`MissingDependencyError` for the first unregistered dep."""
    for key, rule in ruleset.map.items():
        for dep in rule.deps:
            if dep not in ruleset.map:
                raise MissingDependencyError(key, resolution_path=[key, dep]) from None


def _check_factory_arity(key: Key, rule: Rule) -> None:
    """Raise :class:`InvalidFactoryError` when declared deps cannot bind."""
    arity = _factory_arity(rule.make)
    if arity is None:
        return
    required, total, has_varargs = arity
    if len(rule.deps) < required:
        raise InvalidFactoryError(
            key,
            f"factory requires at least {required} args but only {len(rule.deps)} deps declared",
        ) from None
    if len(rule.deps) > total and not has_varargs:
        raise InvalidFactoryError(
            key,
            f"factory accepts at most {total} args but {len(rule.deps)} deps declared",
        ) from None


def _check_cycles(ruleset: RuleSetProtocol) -> None:
    """Raise :class:`DependencyCycleError` when any rule participates in a cycle."""
    for key in ruleset.map:
        try:
            ruleset._check_cycle(key)
        except DependencyCycleError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise DependencyCycleError([key]) from exc


def _rule_meta_map(
    rules_map: Dict[Key, Rule],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Key]]:
    """Build serializable rule metadata plus the repr-key mapping."""
    meta: Dict[str, Dict[str, Any]] = {}
    keys: Dict[str, Key] = {}
    for key in rules_map:
        repr_key = _key_repr(key)
        meta[repr_key] = _rule_meta(rules_map[key])
        keys[repr_key] = key
    return meta, keys


def _plan_nodes(
    order: List[str],
    keys: Dict[str, Key],
    rules_map: Dict[Key, Rule],
) -> Tuple[Dict[Key, int], List[_NodeSpec]]:
    """Create node specs and the key-to-index mapping for ``order``."""
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
    return key_to_idx, nodes


def _freeze_singletons(
    container: Container,
    nodes: List[_NodeSpec],
    allow_post_compile_overrides: bool,
    guardless: bool,
) -> Optional[Dict[Key, Any]]:
    """Pre-resolve singleton constants when the plan cannot change later."""
    if allow_post_compile_overrides and not guardless:
        return None
    frozen: Dict[Key, Any] = {}
    for spec in nodes:
        if spec.lifetime != "singleton":
            continue
        if spec.make is None:
            continue
        args = [frozen[nodes[j].key] for j in spec.deps_idx]
        frozen[spec.key] = spec.make(*args) if args else spec.make()
    container.single.update(frozen)
    object.__setattr__(container, "_compiled_plan", None)
    return frozen


def _maker_eligible(spec: _NodeSpec) -> bool:
    """Return True when a per-node maker can be compiled for ``spec``."""
    return (
        spec.make is not None
        and not spec.yield_provider
        and not spec.async_yield_provider
        and not spec.is_async
        and not spec.nested
    )


def _node_makers(
    nodes: List[_NodeSpec],
    container: Container,
    frozen: Optional[Dict[Key, Any]],
) -> Tuple[List[Optional[Callable[[], Any]]], Dict[Key, Callable[[], Any]]]:
    """Build makers and resolvers for every eligible node."""
    makers: List[Optional[Callable[[], Any]]] = [None] * len(nodes)
    resolvers: Dict[Key, Callable[[], Any]] = {}
    for i, spec in enumerate(nodes):
        if not _maker_eligible(spec):
            continue
        if not all(makers[j] is not None for j in spec.deps_idx):
            continue
        dep_makers = tuple(cast(Callable[[], Any], makers[j]) for j in spec.deps_idx)
        maker = _build_node_maker(spec, dep_makers, container, frozen)
        makers[i] = maker
        resolvers[spec.key] = maker
    return makers, resolvers


def _refine_resolvers(
    nodes: List[_NodeSpec],
    resolvers: Dict[Key, Callable[[], Any]],
    makers: List[Optional[Callable[[], Any]]],
    container: Container,
    frozen: Optional[Dict[Key, Any]],
    nodes_tuple: Tuple[_NodeSpec, ...],
) -> Dict[Key, str]:
    """Replace composed resolvers with frozen/flat ones where possible."""
    resolver_kinds: Dict[Key, str] = dict.fromkeys(resolvers, "composed")
    for i, spec in enumerate(nodes):
        if spec.key not in resolvers:
            continue
        if frozen is not None and spec.lifetime == "singleton":
            resolver_kinds[spec.key] = "frozen"
            continue
        kind, fn = _specialized_resolver(i, nodes_tuple, makers, container, frozen)
        if fn is None:
            continue
        resolvers[spec.key] = fn
        resolver_kinds[spec.key] = kind
    return resolver_kinds


def _specialized_resolver(
    i: int,
    nodes_tuple: Tuple[_NodeSpec, ...],
    makers: List[Optional[Callable[[], Any]]],
    container: Container,
    frozen: Optional[Dict[Key, Any]],
) -> Tuple[str, Optional[Callable[[], Any]]]:
    """Return a frozen/flat resolver for node ``i``, else ``("composed", None)``."""
    if frozen is not None:
        frozen_resolver = _build_frozen_resolver(i, nodes_tuple, frozen)
        if frozen_resolver is not None:
            return frozen_resolver
    flat = _build_flat_resolver(i, nodes_tuple, makers, container, frozen)
    if flat is None:
        return "composed", None
    return flat
