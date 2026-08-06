"""Tests for yield-provider generators with scope finalization."""

import asyncio
from typing import Any, AsyncIterator, Iterator, List

import pytest

from doppy_di import ContainerBuilder, YieldNotCalledError


def test_sync_yield_provider_finalized_on_scope_exit() -> None:
    closed: List[bool] = []

    def make_session() -> Iterator[object]:
        try:
            yield object()
        finally:
            closed.append(True)

    builder = ContainerBuilder()
    builder.service("session", make_session, lifetime="transient")
    container = builder.build()

    with container.scope("req") as scope:
        session = scope.get("session")
        assert session is not None
        assert closed == []

    assert closed == [True]


def test_async_yield_provider_finalized_on_scope_exit() -> None:
    closed: List[bool] = []

    async def make_session() -> AsyncIterator[object]:
        try:
            yield object()
        finally:
            closed.append(True)

    builder = ContainerBuilder()
    builder.service("session", make_session, lifetime="transient")
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            session = await scope.get("session")
            assert session is not None
            assert closed == []

    asyncio.run(main())
    assert closed == [True]


def test_no_yield_provider_no_exit_stack() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    with container.scope("req") as scope:
        scope.get("x")

    assert scope._exit_stack == []


def test_finalization_error_logged_not_raised(caplog: Any) -> None:
    def make_bad() -> Iterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder()
    builder.service("bad", make_bad)
    container = builder.build()

    with container.scope("req") as scope:
        scope.get("bad")

    assert "Error finalizing yield provider" in caplog.text


def test_yield_not_called_raises() -> None:
    def make_empty() -> Iterator[object]:
        if False:
            yield object()  # type: ignore[unreachable]

    builder = ContainerBuilder()
    builder.service("empty", make_empty)
    container = builder.build()

    with container.scope("req") as scope, pytest.raises(YieldNotCalledError):
        scope.get("empty")


def test_async_yield_provider_requires_async_scope() -> None:
    async def make_session() -> AsyncIterator[object]:
        yield object()

    builder = ContainerBuilder()
    builder.service("session", make_session)
    container = builder.build()

    with pytest.raises(TypeError):
        container.get("session")
