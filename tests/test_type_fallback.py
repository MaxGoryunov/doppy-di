"""Tests for configurable type-based fallback resolution (issue #146)."""

import asyncio
from typing import Any, Protocol

import pytest
from typing_extensions import Annotated

from doppy_di import ContainerBuilder, Qualifier, injectable
from doppy_di.auto_wiring import TypeFallbackStrategy, UnresolvableDependencyError
from doppy_di.container import DependencyCycleError, MissingDependencyError


class _Repo:
    pass


@injectable
class _Service:
    def __init__(self, repo: _Repo) -> None:
        self.repo = repo


class _C:
    pass


class _B:
    def __init__(self, c: _C) -> None:
        self.c = c


@injectable
class _A:
    def __init__(self, b: _B) -> None:
        self.b = b


@injectable
class _NeedsInt:
    def __init__(self, x: int) -> None:
        self.x = x


class _DB:
    def __init__(self) -> None:
        # Fixture stub only; no behavior needed for fallback tests.
        pass


@injectable
class _QualifiedSvc:
    def __init__(self, db: Annotated[_DB, Qualifier("read")]) -> None:
        self.db = db


@injectable
class _Ambiguous:
    def __init__(self, db: _DB) -> None:
        self.db = db


class _CA:
    def __init__(self, b: Any) -> None:
        self.b = b


class _CB:
    def __init__(self, a: _CA) -> None:
        self.a = a


_CA.__init__.__annotations__ = {"b": _CB}


class _Greeter(Protocol):
    def greet(self) -> str: ...


@injectable
class _GreeterSvc:
    def __init__(self, g: _Greeter) -> None:
        self.g = g


def test_default_off_raises_for_plain_concrete() -> None:
    container = ContainerBuilder().build()
    with pytest.raises(
        (MissingDependencyError, UnresolvableDependencyError), match=r"Repo|ependency"
    ):
        container.get(_Service)


def test_concrete_only_resolves_chain() -> None:
    builder = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy())
    container = builder.build()
    svc = container.get(_Service)
    assert isinstance(svc.repo, _Repo)


def test_nested_chain_and_explicit_override_wins() -> None:
    builder = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy())
    marker = _C()
    builder.value(_C, marker)
    container = builder.build()
    assert container.get(_A).b.c is marker


def test_builtins_any_union_rejected() -> None:
    container = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy()).build()
    with pytest.raises(UnresolvableDependencyError, match="Unresolvable dependency"):
        container.get(_NeedsInt)


def test_qualifier_conflict_not_resolved_implicitly() -> None:
    builder = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy())
    builder.value((_DB, "read"), _DB())
    builder.value((_DB, "write"), _DB())
    container = builder.build()
    assert isinstance(container.get(_QualifiedSvc).db, _DB)
    with pytest.raises(UnresolvableDependencyError, match="Unresolvable dependency"):
        container.get(_Ambiguous)


def test_cycle_detected_with_chain() -> None:
    container = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy()).build()
    with pytest.raises(DependencyCycleError, match="Cycle"):
        container.get(_CA)


def test_async_factory_supported() -> None:
    container = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy()).build()
    svc = asyncio.run(container.aget(_Service))
    assert isinstance(svc.repo, _Repo)


def test_protocol_ambiguous_rejected_by_default() -> None:
    container = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy()).build()
    with pytest.raises(UnresolvableDependencyError, match="Unresolvable dependency"):
        container.get(_GreeterSvc)


def test_set_strategy_builder_method() -> None:
    builder = ContainerBuilder()
    builder.set_autowiring_strategy(TypeFallbackStrategy(allow_concrete_classes=True))
    assert isinstance(builder.build().get(_Service).repo, _Repo)


def test_strict_mode_allows_only_injectable() -> None:
    @injectable
    class NeedsRepo:
        def __init__(self, repo: _Repo) -> None:
            self.repo = repo

    strict = TypeFallbackStrategy(allow_concrete_classes=False)
    assert strict.should_attempt(_C) is not None
    assert strict.should_attempt(_Service) is None
    container = ContainerBuilder(autowiring_strategy=strict).build()
    with pytest.raises(UnresolvableDependencyError, match=r"Unresolvable dependency"):
        container.get(_Service)

    @injectable
    class NeedsPlain:
        def __init__(self, c: _C) -> None:
            self.c = c

    with pytest.raises(UnresolvableDependencyError, match=r"Unresolvable dependency"):
        container.get(NeedsPlain)


def test_allow_builtins_and_protocols_opt_in() -> None:
    s = TypeFallbackStrategy(allow_builtins=True, allow_protocols=True)
    assert s.should_attempt(int) is None
    assert s.should_attempt(_Greeter) is None
    assert TypeFallbackStrategy().should_attempt(int) is not None


def test_reject_any_union_generic_non_type() -> None:
    from typing import Dict, List, Optional

    s = TypeFallbackStrategy()
    assert s.should_attempt(None) is not None
    assert s.should_attempt(int | str) is not None
    assert s.should_attempt(Optional[int]) is not None
    assert s.should_attempt(List[int]) is not None
    assert s.should_attempt(Dict[str, int]) is not None
    assert s.should_attempt("not-a-type") is not None  # type: ignore[arg-type]
    assert s.should_attempt(Annotated[int, Qualifier("q")]) is not None
    assert s.should_attempt(Annotated[_Repo, Qualifier("q")]) is None


def test_max_depth_guard() -> None:
    s = TypeFallbackStrategy(max_depth=1)
    assert s.check_depth(1) is None
    assert s.check_depth(2) is not None


def test_abstract_without_protocol_flag_rejected() -> None:
    import abc

    class Base(abc.ABC):
        @abc.abstractmethod
        def run(self) -> None: ...

    assert TypeFallbackStrategy().should_attempt(Base) is not None
    assert TypeFallbackStrategy(allow_protocols=True).should_attempt(Base) is None


def test_missing_annotation_and_unresolvable_target() -> None:
    from doppy_di.auto_wiring import (
        _fallback_rule_for,
        _target_of_lookup,
    )

    class NoAnn:
        def __init__(self, x):  # type: ignore[no-untyped-def]
            self.x = x

    assert _target_of_lookup("key") is None
    assert _target_of_lookup((_Repo, "q")) is _Repo
    assert _target_of_lookup("key") is None
    s = TypeFallbackStrategy()
    with pytest.raises(UnresolvableDependencyError, match=r"Unresolvable dependency"):
        _fallback_rule_for("key", s, [])
    with pytest.raises(UnresolvableDependencyError, match=r"Unresolvable dependency"):
        _fallback_rule_for(NoAnn, s, [])


def test_fallback_deps_edge_cases() -> None:
    from doppy_di.auto_wiring import _fallback_deps

    class WithDefault:
        def __init__(self, x: int = 1) -> None:
            self.x = x

    assert _fallback_deps(WithDefault) == ()
    assert _fallback_deps(int) == ()


def test_qualified_lookup_key_preserved() -> None:
    from doppy_di.auto_wiring import _fallback_rule_for

    rule = _fallback_rule_for((_Repo, "special"), TypeFallbackStrategy(), [])
    assert rule.key == (_Repo, "special")
    assert rule.deps == ()


def test_builder_ctor_strategy_and_docs_example() -> None:
    builder = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy(max_depth=8))
    assert isinstance(builder.build().get(_Service).repo, _Repo)


def test_empty_annotated_and_generic_origin_branches() -> None:
    from typing import Callable

    from doppy_di.auto_wiring import _annotation_key

    s = TypeFallbackStrategy()
    assert s.should_attempt(int | str) is not None
    assert "explicit rule" in (s.should_attempt(Callable[[int], int]) or "")
    assert _annotation_key(int) is int


def test_depth_overflow_blocks_fallback() -> None:
    s = TypeFallbackStrategy(max_depth=0)

    @injectable
    class Top:
        def __init__(self, repo: _Repo) -> None:
            self.repo = repo

    container = ContainerBuilder(autowiring_strategy=s).build()
    with pytest.raises(UnresolvableDependencyError, match=r"Unresolvable dependency"):
        container.get(Top)
