"""Coverage tests for compiled-plan flat and generic resolver permutations.

These exercise the arity/kind combinatorial paths emitted by the fast-path
resolver builder (issue #122): root arities 0-3, prelude ("p") singleton slots,
one-dep ("l1") and two-dep ("l2") leaf slots, the literal2 root layout, and the
fallback generic expression path.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from doppy_di import ContainerBuilder


def _build(
    extras: Iterable[tuple[str, Callable[..., Any], tuple[Any, ...]]],
    root_make: Callable[..., Any],
    root_deps: tuple[Any, ...],
    root_lifetime: str = "transient",
    allow_frozen: bool = True,
) -> Any:
    builder = ContainerBuilder()
    for key in ("S1", "S2", "S3"):
        builder.value(key, {"S1": 11, "S2": 22, "S3": 33}[key])
    for key, make, deps in extras:
        builder.service(key, make, lifetime="transient", deps=list(deps))
    builder.service("root", root_make, lifetime=root_lifetime, deps=list(root_deps))
    container = builder.build()
    if allow_frozen:
        return container.compile()
    return container.compile(allow_post_compile_overrides=False)


def _l1(*deps: str) -> Callable[..., Any]:
    def make(*args: int) -> int:
        return args[0] + 1

    return make


def _l2(*deps: str) -> Callable[..., Any]:
    def make(*args: int) -> int:
        return args[0] + args[1]

    return make


def _l3(*deps: str) -> Callable[..., Any]:
    def make(*args: int) -> int:
        return args[0] + args[1] + args[2]

    return make


def _root(*args: int) -> int:
    return 1000 + sum(args)


# --- flat path: literal root, arity 2 ---------------------------------------


def test_flat_arity2_pp() -> None:
    plan = _build([], _root, ("S1", "S2"))
    assert plan.get("root") == 1000 + 11 + 22
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity2_pl() -> None:
    plan = _build([("L1", _l1("S2"), ("S2",))], _root, ("S1", "L1"))
    assert plan.get("root") == 1000 + 11 + 23
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity2_lp() -> None:
    plan = _build([("L1", _l1("S2"), ("S2",))], _root, ("L1", "S1"))
    assert plan.get("root") == 1000 + 23 + 11
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity2_ll() -> None:
    plan = _build(
        [("L1", _l1("S1"), ("S1",)), ("L2", _l1("S2"), ("S2",))],
        _root,
        ("L1", "L2"),
    )
    assert plan.get("root") == 1000 + 12 + 23
    assert plan.resolver_kinds["root"] == "flat"


# --- flat path: literal root, arity 3 ---------------------------------------


def test_flat_arity3_ppp() -> None:
    plan = _build([], _root, ("S1", "S2", "S3"))
    assert plan.get("root") == 1000 + 11 + 22 + 33
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_ppl() -> None:
    plan = _build([("L1", _l1("S3"), ("S3",))], _root, ("S1", "S2", "L1"))
    assert plan.get("root") == 1000 + 11 + 22 + 34
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_plp() -> None:
    plan = _build([("L1", _l1("S2"), ("S2",))], _root, ("S1", "L1", "S3"))
    assert plan.get("root") == 1000 + 11 + 23 + 33
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_pll() -> None:
    plan = _build(
        [("L1", _l1("S2"), ("S2",)), ("L2", _l1("S3"), ("S3",))],
        _root,
        ("S1", "L1", "L2"),
    )
    assert plan.get("root") == 1000 + 11 + 23 + 34
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_lpp() -> None:
    plan = _build([("L1", _l1("S1"), ("S1",))], _root, ("L1", "S2", "S3"))
    assert plan.get("root") == 1000 + 12 + 22 + 33
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_lpl() -> None:
    plan = _build(
        [("L1", _l1("S1"), ("S1",)), ("L2", _l1("S3"), ("S3",))],
        _root,
        ("L1", "S2", "L2"),
    )
    assert plan.get("root") == 1000 + 12 + 22 + 34
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_llp() -> None:
    plan = _build(
        [("L1", _l1("S1"), ("S1",)), ("L2", _l1("S2"), ("S2",))],
        _root,
        ("L1", "L2", "S3"),
    )
    assert plan.get("root") == 1000 + 12 + 23 + 33
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_arity3_lll() -> None:
    plan = _build(
        [("L1", _l1("S1"), ("S1",)), ("L2", _l1("S2"), ("S2",)), ("L3", _l1("S3"), ("S3",))],
        _root,
        ("L1", "L2", "L3"),
    )
    assert plan.get("root") == 1000 + 12 + 23 + 34
    assert plan.resolver_kinds["root"] == "flat"


# --- flat path: literal2 root, l2 leaf slots --------------------------------


def test_flat_literal2_arity2() -> None:
    plan = _build(
        [
            ("LA", _l2("S1", "S2"), ("S1", "S2")),
            ("LB", _l2("S2", "S3"), ("S2", "S3")),
        ],
        _root,
        ("LA", "LB"),
    )
    assert plan.get("root") == 1000 + 33 + 55
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_literal2_arity3() -> None:
    plan = _build(
        [
            ("LA", _l2("S1", "S2"), ("S1", "S2")),
            ("LB", _l2("S2", "S3"), ("S2", "S3")),
            ("LC", _l2("S1", "S3"), ("S1", "S3")),
        ],
        _root,
        ("LA", "LB", "LC"),
    )
    assert plan.get("root") == 1000 + 33 + 55 + 44
    assert plan.resolver_kinds["root"] == "flat"


def test_flat_literal2_mixed_p() -> None:
    plan = _build(
        [("LA", _l2("S2", "S3"), ("S2", "S3"))],
        _root,
        ("S1", "LA"),
    )
    assert plan.get("root") == 1000 + 11 + 55
    assert plan.resolver_kinds["root"] == "flat"


# --- generic path ------------------------------------------------------------


def test_generic_mixed_arity_root2() -> None:
    plan = _build(
        [
            ("LA", _l1("S2"), ("S2",)),
            ("LB", _l2("S1", "S2"), ("S1", "S2")),
        ],
        _root,
        ("LA", "LB"),
    )
    assert plan.get("root") == 1000 + 23 + 33
    assert plan.resolver_kinds["root"] == "generic"


def test_generic_arity3_with_l3_leaf() -> None:
    plan = _build(
        [
            ("L1", _l1("S1"), ("S1",)),
            ("L2", _l2("S2", "S3"), ("S2", "S3")),
            ("L3", _l3("S1", "S2", "S3"), ("S1", "S2", "S3")),
        ],
        _root,
        ("L1", "L2", "L3"),
    )
    assert plan.get("root") == 1000 + 12 + 55 + 66
    assert plan.resolver_kinds["root"] == "generic"


def test_generic_root_arity4() -> None:
    plan = _build(
        [
            ("LA", _l1("S1"), ("S1",)),
            ("LB", _l1("S2"), ("S2",)),
            ("LC", _l1("S3"), ("S3",)),
            ("LD", _l1("S1"), ("S1",)),
        ],
        _root,
        ("LA", "LB", "LC", "LD"),
    )
    assert plan.get("root") == 1000 + 12 + 23 + 34 + 12
    assert plan.resolver_kinds["root"] == "generic"


def test_generic_root_no_deps() -> None:
    plan = _build([], _root, ())
    assert plan.get("root") == 1000
    assert plan.resolver_kinds["root"] == "generic"


def test_generic_root_with_three_dep_leaf_and_p() -> None:
    plan = _build(
        [("L0", _l3("S1", "S2", "S3"), ("S1", "S2", "S3"))],
        _root,
        ("L0", "S1"),
    )
    assert plan.get("root") == 1000 + 66 + 11
    assert plan.resolver_kinds["root"] == "generic"


# --- frozen / singleton variations -------------------------------------------


class _SingletonChild:
    def __init__(self) -> None:
        self.value = 7


def test_frozen_singleton_root_fallback() -> None:
    plan = _build([], lambda: 5, (), root_lifetime="singleton", allow_frozen=False)
    assert plan.get("root") == 5
    assert plan.frozen is True


def test_frozen_identity_across_singleton_children() -> None:
    builder = ContainerBuilder()
    builder.value("S1", 11)
    builder.service("cont", _SingletonChild, lifetime="singleton")
    container = builder.build()
    plan = container.compile(allow_post_compile_overrides=False)
    a = plan.get("cont")
    b = plan.get("cont")
    assert a is b
    assert a is container.get("cont")


def _l4(*deps: str) -> Callable[..., Any]:
    def make(*args: int) -> int:
        return args[0] + args[1] + args[2] + args[3]

    return make


_L2_MARKS: dict[str, int] = {"S1": 11, "S2": 22, "S3": 33}


def _l2_test(marks: tuple[str, ...], expected: int, decoy: bool = False) -> None:
    builder = ContainerBuilder()
    for k, v in _L2_MARKS.items():
        builder.value(k, v)
    leaf_make: dict[str, tuple[str, ...]] = {
        "LA": ("S2", "S3"),
        "LB": ("S1", "S2"),
        "LC": ("S1", "S3"),
    }
    for m in marks:
        if m.startswith("S"):
            continue
        deps = leaf_make[m]
        builder.service(m, _l2(*deps), lifetime="transient", deps=list(deps))
    if decoy:
        builder.service("D", _l2("S1", "S2"), lifetime="transient", deps=["S1", "S2"])
    builder.service("root", _root, lifetime="transient", deps=list(marks))
    plan = builder.build().compile()
    assert plan.get("root") == expected
    assert plan.resolver_kinds["root"] == "flat"


def test_literal2_arity2_pp() -> None:
    _l2_test(("S1", "S2"), 1000 + 11 + 22, decoy=True)


def test_literal2_arity2_pl() -> None:
    _l2_test(("S1", "LA"), 1000 + 11 + 55)


def test_literal2_arity2_lp() -> None:
    _l2_test(("LA", "S1"), 1000 + 11 + 55)


def test_literal2_arity2_ll() -> None:
    _l2_test(("LA", "LB"), 1000 + 55 + 33)


def test_literal2_arity3_ppp() -> None:
    _l2_test(("S1", "S2", "S3"), 1000 + 11 + 22 + 33, decoy=True)


def test_literal2_arity3_ppl() -> None:
    _l2_test(("S1", "S2", "LA"), 1000 + 11 + 22 + 55)


def test_literal2_arity3_plp() -> None:
    _l2_test(("S1", "LA", "S3"), 1000 + 11 + 55 + 33)


def test_literal2_arity3_pll() -> None:
    _l2_test(("S1", "LA", "LB"), 1000 + 11 + 55 + 33)


def test_literal2_arity3_lpp() -> None:
    _l2_test(("LA", "S2", "S3"), 1000 + 55 + 22 + 33)


def test_literal2_arity3_lpl() -> None:
    _l2_test(("LA", "S2", "LB"), 1000 + 55 + 22 + 33)


def test_literal2_arity3_llp() -> None:
    _l2_test(("LA", "LB", "S3"), 1000 + 55 + 33 + 33)


def test_literal2_arity3_lll() -> None:
    _l2_test(("LA", "LB", "LC"), 1000 + 55 + 33 + 44)


def test_generic_leaf_with_four_deps_uses_varargs() -> None:
    plan = _build(
        [("L4", _l4("S1", "S2", "S3", "S1"), ("S1", "S2", "S3", "S1"))],
        _root,
        ("L4",),
    )
    assert plan.get("root") == 1000 + (11 + 22 + 33 + 11)
    assert plan.resolver_kinds["root"] == "generic"


def test_frozen_flat_eligible_singleton_root() -> None:
    plan = _build([], _root, ("S1", "S2"), root_lifetime="singleton", allow_frozen=False)
    assert plan.get("root") == 1000 + 11 + 22
    assert plan.resolver_kinds["root"] == "frozen"
