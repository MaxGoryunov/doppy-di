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
