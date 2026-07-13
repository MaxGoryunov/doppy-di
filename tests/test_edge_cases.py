"""Tests for edge cases and hypotheses about potential bugs."""

import threading
from typing import Any, Dict, List

import pytest

from container import ContainerBuilder, CycleError, Key, NestedRuleError, Rule, ServiceNotFoundError
from devkit.logging import LoggingContainer
from devkit.nested import NestedRules, SameValuePolicy
from devkit.policy import ChildrenFirstPolicy
from devkit.validation import ValidatingContainer, ValidationRunner

# ── H1: Missing key panic ──────────────────────────────────────────────


def test_get_missing_key_raises_error() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(ServiceNotFoundError):
        container.get("nonexistent")


def test_ruleset_find_missing_raises_error() -> None:
    from container import RuleSet

    rules = RuleSet()
    with pytest.raises(ServiceNotFoundError):
        rules.find("missing")


def test_get_or_none_missing_returns_none() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    assert container.get_or_none("nonexistent") is None


def test_get_or_none_registered_returns_value() -> None:
    builder = ContainerBuilder()
    builder.value("x", 42)
    container = builder.build()

    assert container.get_or_none("x") == 42


# ── H2: Double-register silence ────────────────────────────────────────


def test_duplicate_key_overwrites_silently() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.value("x", 2)

    container = builder.build()
    assert container.get("x") == 2


def test_duplicate_key_ruleset_overwrites() -> None:
    from container import RuleSet

    rules = RuleSet()
    rules.add("k", Rule("k", lambda: 1))
    rules.add("k", Rule("k", lambda: 2))
    assert rules.find("k").make() == 2


# ── H3: Stale scope on name reuse ──────────────────────────────────────


def test_scope_reuse_has_stale_cache() -> None:
    builder = ContainerBuilder()
    builder.service("x", lambda: object(), lifetime="transient")
    container = builder.build()

    scope = container.scope("req")
    with scope:
        a = scope.get("x")

    # Re-acquire same scope name — cache may still hold old value
    scope2 = container.scope("req")
    b = scope2.get("x")

    # If scope reuses old cache, a is b -> stale cache leak
    # Hypothesis: scope cache was cleared on exit, so a is not b
    assert a is not b


def test_scope_reuse_before_exit_keeps_cache() -> None:
    """Scope cache persists during the with-block, but re-getting
    the same scope name after exit creates a fresh scope or keeps
    stale cache depending on implementation."""
    builder = ContainerBuilder()
    builder.service("x", lambda: object(), lifetime="transient")
    container = builder.build()

    # Simulate two sequential with-blocks sharing scope name
    with container.scope("req") as s1:
        a = s1.get("x")

    # After exit, cache cleared
    with container.scope("req") as s2:
        b = s2.get("x")

    assert a is not b


# ── H4: Override of unresolved singleton ───────────────────────────────


def test_override_unresolved_singleton_then_get_after_exit() -> None:
    call_count = 0

    def make_db() -> Dict[str, int]:
        nonlocal call_count
        call_count += 1
        return {"db": call_count}

    builder = ContainerBuilder()
    builder.service("db", make_db, lifetime="singleton")
    container = builder.build()

    fake_db = {"db": 999}

    with container.override("db", fake_db):
        resolved = container.get("db")
        assert resolved is fake_db

    # After exit, override removed. get() triggers factory.
    after = container.get("db")
    assert after == {"db": 1}
    assert call_count == 1
    assert after is not fake_db


# ── H5: Unknown lifetime no error ──────────────────────────────────────


def test_unknown_lifetime_not_cached() -> None:
    builder = ContainerBuilder()
    builder.service("x", lambda: object(), lifetime="per_request")  # nonsense lifetime
    container = builder.build()

    a = container.get("x")
    b = container.get("x")

    # If lifetime is unknown, container treats it as transient by default
    # but the code only caches "singleton" — so a is b possible only if
    # something else caches it. Hypothesis: a is not b for unknown lifetime
    # because only "singleton" triggers caching.
    # Actually container.py line 275 checks rule.lifetime == "singleton",
    # and _cache_nested_aliases runs always. So object may be cached
    # via alias path. Let's check with a plain key:
    assert a is not b


def test_unknown_lifetime_not_cached_for_value() -> None:
    """value() sets lifetime='singleton' explicitly, so this covers
    the case where user manually creates Rule with bad lifetime string."""
    from container import Rule, RuleSet

    rules = RuleSet()
    rules.add("x", Rule("x", lambda: object(), lifetime="bad_value"))
    # Test via ContainerBuilder raw:
    builder = ContainerBuilder()
    builder.rules.add("x", Rule("x", lambda: object(), lifetime="weird"))
    c = builder.build()

    a = c.get("x")
    b = c.get("x")
    assert a is not b


# ── H6: LoggingContainer catches BaseException ─────────────────────────


def test_logging_container_base_exception_not_caught() -> None:
    """LoggingContainer.get() wraps in except Exception — does NOT catch
    BaseException subclasses like KeyboardInterrupt, SystemExit."""

    builder = ContainerBuilder()
    builder.service("x", lambda: (_ for _ in ()).throw(SystemExit(1)))
    base = builder.build()

    events: List[str] = []

    def log(msg: str) -> None:
        events.append(msg)

    container = LoggingContainer(base, log)

    with pytest.raises(SystemExit):
        container.get("x")

    # SystemExit is not Exception, so log should NOT contain error message
    assert not any("error" in e for e in events)


# ── H7: Nested validation re-enters parent ────────────────────────────


def test_nested_validation_chain_no_recursion() -> None:
    """A deep chain of nested children in ChildrenFirstPolicy
    should NOT cause infinite recursion — chain is finite.
    before_resolve walks: root -> (root,a) -> ((root,a),b) -> ...
    and terminates when a key has no children."""

    class Node:
        def __init__(self, child: Any = None) -> None:
            self.child = child

    builder = ContainerBuilder()
    builder.service("root", lambda: Node(), lifetime="transient")
    base = builder.build()

    # Build a chain: root -> (root, "a") -> ((root, "a"), "b") -> ...
    nested = NestedRules()
    policy_nested: Dict[Key, List[str]] = {}
    prev_key: Key = "root"
    for _i, letter in enumerate("abc"):
        child_key = (prev_key, letter)
        policy_nested[prev_key] = [letter]
        nested.add_nested(
            prev_key,
            letter,
            Rule(child_key, lambda: Node(), lifetime="transient", deps=()),
            base.config.ruleset,
        )
        prev_key = child_key

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested=policy_nested),
        ValidationRunner(),
        nested,
    )

    # Should resolve without RecursionError
    # validate_nested will fail because Node has no 'a' attr,
    # but that's a separate issue — we test no infinite loop
    try:
        container.get("root")
    except RecursionError:
        pytest.fail("Deep nested chain caused infinite recursion")
    except NestedRuleError:
        pass  # expected: Node has no 'a' attribute


# ── H8: Partial state on cycle error ──────────────────────────────────


def test_cycle_error_leaves_partial_state() -> None:
    from container import RuleSet

    rules = RuleSet()
    rules.add("a", Rule("a", lambda: 1))

    rules.add("b", Rule("b", lambda a: a, deps=("a",)))
    with pytest.raises(CycleError):
        rules.add("a", Rule("a", lambda b: b, deps=("b",)))

    # After cycle error from adding 'a' again, both 'a' and 'b' may
    # linger in map/graph despite invalid state
    assert "a" in rules.map
    assert "b" in rules.map  # left behind from earlier add


# ── H9: Alias to missing target ────────────────────────────────────────


def test_alias_to_missing_target_fails_at_resolve() -> None:
    builder = ContainerBuilder()
    builder.alias("a", "b")
    container = builder.build()

    with pytest.raises(KeyError):
        container.get("a")


def test_alias_to_missing_target_does_not_fail_at_build() -> None:
    """The builder accepts alias to nonexistent key — error only at resolve."""
    builder = ContainerBuilder()
    builder.alias("a", "b")
    # Should not raise
    container = builder.build()
    assert container.has("a")
    assert not container.has("b")


# ── H10: Thread-safety hole in singleton ──────────────────────────────


def test_singleton_thread_safety_race() -> None:
    call_count = 0
    lock = threading.Lock()

    def make_obj() -> Dict[str, int]:
        nonlocal call_count
        with lock:
            call_count += 1
            return {"id": call_count}

    builder = ContainerBuilder()
    builder.service("x", make_obj, lifetime="singleton")
    container = builder.build()

    results: List[Any] = []
    errors: List[Exception] = []
    barrier = threading.Barrier(10)

    def get_x() -> None:
        barrier.wait()
        try:
            results.append(container.get("x"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=get_x) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # If thread-safe, factory runs exactly once
    # If not, factory runs more than once and different objects appear
    ids = {r["id"] for r in results}
    assert len(ids) == 1, f"Expected 1 unique object, got {len(ids)}: {ids}"


# ── H11: SameValuePolicy raises in custom __eq__ ──────────────────────


def test_same_value_policy_with_broken_eq() -> None:
    class Broken:
        def __init__(self, val: int) -> None:
            self.val = val

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("eq broken")

    policy = SameValuePolicy()
    with pytest.raises(RuntimeError, match="eq broken"):
        policy.check(Broken(1), Broken(1))


def test_same_value_policy_with_none() -> None:
    """None == None is True, should not crash."""
    policy = SameValuePolicy()
    assert policy.check(None, None)


def test_same_value_policy_with_different_types() -> None:
    """1 == True in Python, but this may be surprising."""
    policy = SameValuePolicy()
    assert policy.check(1, True)


# ── H12: ChildrenFirstPolicy nested recursion via ctx.get ──────────────


def test_children_first_policy_deep_nested_recursion() -> None:
    """If a child listed in ChildrenFirstPolicy also has children,
    before_resolve recurses via ctx.get -> container.get -> policy.before_resolve."""

    class Deep:
        def __init__(self, child: Any = None) -> None:
            self.child = child

    builder = ContainerBuilder()
    builder.service("a", lambda: Deep(), lifetime="transient")
    builder.service("b", lambda: Deep(), lifetime="transient")
    builder.service("c", lambda: Deep(), lifetime="transient")
    # Register nested keys so they exist in ruleset
    builder.rules.add(
        ("a", "b"),
        Rule(("a", "b"), lambda: Deep(), lifetime="transient"),
    )
    builder.rules.add(
        ("b", "c"),
        Rule(("b", "c"), lambda: Deep(), lifetime="transient"),
    )
    base = builder.build()

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"a": ["b"], "b": ["c"]}),
        ValidationRunner(),
        None,
    )

    # If policy recurses infinitely, this raises RecursionError
    # If it works, it resolves fine
    try:
        a = container.get("a")
        assert hasattr(a, "child")
        assert a.child is None  # "b" was resolved but Deep.child is None
    except RecursionError:
        pytest.fail("ChildrenFirstPolicy caused infinite recursion with deep nested chain")


def test_children_first_policy_self_referential() -> None:
    """Child key equals parent key — direct infinite recursion."""

    builder = ContainerBuilder()
    builder.value("x", 1)
    builder.rules.add(
        ("x", "x"),
        Rule(("x", "x"), lambda: 1, lifetime="transient"),
    )
    base = builder.build()

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"x": ["x"]}),
        ValidationRunner(),
        None,
    )

    val = container.get("x")
    assert val == 1


# ── Extra: edge case on Scope.__enter__ re-entry ───────────────────────


def test_scope_double_enter() -> None:
    """Scope.__enter__ called twice on same scope object should not crash."""
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    scope = container.scope("double")
    with scope:
        a = scope.get("x")

    # Re-enter same scope object
    with scope:
        b = scope.get("x")

    assert a == b


def test_scope_enter_without_exit_leaks() -> None:
    """If user never calls __exit__, cache persists forever."""
    builder = ContainerBuilder()
    builder.service("x", lambda: object(), lifetime="transient")
    container = builder.build()

    scope = container.scope("leak")
    scope.__enter__()
    a = scope.get("x")
    # Forgot __exit__
    scope.cache.clear()
    b = scope.get("x")
    assert a is not b
