"""Tests for assisted injection (partial external parameters via builder)."""

import asyncio
from typing import Any

import pytest

from doppy_di import Container, ContainerBuilder, Depends, MissingAnnotationError, inject
from doppy_di.container import Scope
from doppy_di.inject import External, MissingExternalArgumentError, assisted
from doppy_di.providers import Assisted, Scoped


class Repo:
    pass


class UserService:
    pass


class Missing:
    pass


@pytest.fixture
def container() -> Any:
    builder = ContainerBuilder()
    builder.value(Repo, Repo())
    builder.value(UserService, UserService())
    return builder.build()


def test_assisted_standalone_resolves_injected_and_external(
    container: Any,
) -> None:
    def make(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    factory = assisted(make, container=container)
    repo, user_id = factory.build(user_id=7)
    assert isinstance(repo, Repo)
    assert user_id == 7


def test_assisted_standalone_reuses_across_calls(container: Any) -> None:
    def make(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    factory = assisted(make, container=container)
    _, first = factory.build(user_id=1)
    _, second = factory.build(user_id=2)
    assert first == 1
    assert second == 2


def test_assisted_standalone_async(container: Any) -> None:
    def make(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    async def run() -> "tuple[Repo, int]":
        factory = assisted(make, container=container)
        repo, user_id = await factory.abuild(user_id=7)
        return repo, user_id

    repo, user_id = asyncio.run(run())
    assert isinstance(repo, Repo)
    assert user_id == 7


def test_assisted_missing_external_raises(container: Any) -> None:
    def make(user_id: int = External()) -> int:
        return user_id

    factory = assisted(make, container=container)
    with pytest.raises(MissingExternalArgumentError):
        factory.build()


def test_assisted_unknown_kwarg_raises(container: Any) -> None:
    def make(user_id: int = External()) -> int:
        return user_id

    factory = assisted(make, container=container)
    with pytest.raises(TypeError):
        factory.build(user_id=1, unexpected=2)


def test_assisted_unannotated_param_raises(container: Any) -> None:
    def make(a) -> Any:  # type: ignore[no-untyped-def]
        return a

    factory = assisted(make, container=container)
    with pytest.raises(MissingAnnotationError):
        factory.build()


def test_assisted_provider_resolves_from_container(container: Any) -> None:
    def make(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    container.handler = Assisted(make)
    builder = container.get("handler")
    repo, user_id = builder.build(user_id=3)
    assert isinstance(repo, Repo)
    assert user_id == 3


def test_assisted_provider_with_scoped_dep() -> None:
    class Session(list):  # type: ignore[type-arg]
        pass

    container = Container()
    container.session = Scoped(Session, Scope.REQUEST)

    def make(session: Session, user_id: int = External()) -> Session:
        session.append(user_id)
        return session

    container.handler = Assisted(make)
    with container.scope("req") as s:
        first = s.get("handler").build(user_id=1)
        second = s.get("handler").build(user_id=2)
    assert first is second


def test_assisted_provider_compiles(container: Any) -> None:
    def make(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    container.handler = Assisted(make)
    container.compile()
    builder = container.get("handler")
    repo, user_id = builder.build(user_id=5)
    assert isinstance(repo, Repo)
    assert user_id == 5


def test_assisted_rejects_non_transient_lifetime() -> None:
    def make(user_id: int = External()) -> int:
        return user_id

    with pytest.raises(ValueError, match="transient"):
        Assisted(make, lifetime="singleton")


def test_inject_with_external_param(container: Any) -> None:
    @inject(container=container)
    def handle(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    repo, user_id = handle(user_id=9)
    assert isinstance(repo, Repo)
    assert user_id == 9


def test_inject_external_missing_raises(container: Any) -> None:
    @inject(container=container)
    def handle(user_id: int = External()) -> int:
        return user_id

    with pytest.raises(MissingExternalArgumentError):
        handle()


def test_inject_external_with_scope(container: Any) -> None:
    @inject(container=container, scope="req")
    def handle(repo: Repo, user_id: int = External()) -> tuple[Repo, int]:
        return repo, user_id

    repo, user_id = handle(user_id=11)
    assert isinstance(repo, Repo)
    assert user_id == 11


def test_assisted_ignores_depends_with_external(container: Any) -> None:
    def make(
        repo: Repo = Depends(),  # noqa: B008
        user_id: int = External(),
    ) -> tuple[Repo, int]:
        return repo, user_id

    factory = assisted(make, container=container)
    repo, user_id = factory.build(user_id=4)
    assert isinstance(repo, Repo)
    assert user_id == 4


def test_assisted_provider_class_factory(container: Any) -> None:
    class Handler:
        def __init__(self, repo: Repo, user_id: int = External()) -> None:
            self.repo = repo
            self.user_id = user_id

    container.handler = Assisted(Handler)
    handler = container.get("handler").build(user_id=42)
    assert isinstance(handler, Handler)
    assert isinstance(handler.repo, Repo)
    assert handler.user_id == 42
