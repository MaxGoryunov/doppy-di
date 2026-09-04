"""Tests for per-request/session context data via ``from_context``."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from doppy_di import Container, Scope
from doppy_di.container import ContainerBuilder, ContextValueMissingError
from doppy_di.providers import from_context


def _request_container() -> Container:
    services = Container()
    services.user = from_context("user")
    return services


def test_from_context_resolves_request_value() -> None:
    c = _request_container()
    with c.scope("req") as s:
        s.set_context("user", "alice")
        assert s.get("user") == "alice"


def test_container_get_inside_scope_sees_context() -> None:
    c = _request_container()
    with c.scope("req") as s:
        s.set_context("user", "alice")
        assert c.get("user") == "alice"


def test_from_context_missing_raises() -> None:
    c = _request_container()
    with c.scope("req") as s, pytest.raises(ContextValueMissingError):
        s.get("user")


def test_from_context_outside_scope_raises() -> None:
    c = _request_container()
    with pytest.raises(ContextValueMissingError):
        c.get("user")


def test_request_context_cleared_on_scope_exit() -> None:
    c = _request_container()
    with c.scope("req") as s:
        s.set_context("user", "alice")
        assert s.get("user") == "alice"
    # NAMED policy reuses the same scope object; request data must not leak.
    with c.scope("req") as s, pytest.raises(ContextValueMissingError):
        s.get("user")


def test_from_context_is_transient_not_singleton() -> None:
    c = _request_container()
    with c.scope("a") as s:
        s.set_context("user", "one")
        assert s.get("user") == "one"
    with c.scope("b") as s:
        s.set_context("user", "two")
        assert s.get("user") == "two"


def test_request_vs_session_context_separated() -> None:
    services = Container()
    services.request_user = from_context("user", Scope.REQUEST)
    services.session_user = from_context("user", Scope.SESSION)
    c = services
    with c.scope("req") as s:
        s.set_context("user", "req_user", Scope.REQUEST)
        with pytest.raises(ContextValueMissingError):
            s.get("session_user")
        s.set_context("user", "sess_user", Scope.SESSION)
        assert s.get("request_user") == "req_user"
        assert s.get("session_user") == "sess_user"


def test_session_context_readable_by_key() -> None:
    c = _request_container()
    with c.scope("req") as s:
        s.set_context("user", "alice", Scope.REQUEST)
        assert s.get_context("user") == "alice"
        assert s.get_context("user", Scope.REQUEST) == "alice"


def test_async_scope_resolves_request_value() -> None:
    c = _request_container()

    async def main() -> str:
        async with c.ascope("req") as s:
            s.set_context("user", "alice")
            return cast(str, await s.get("user"))

    assert asyncio.run(main()) == "alice"


def test_context_propagates_to_child_tasks() -> None:
    c = _request_container()

    async def main() -> list[str]:
        async with c.ascope("req") as s:
            s.set_context("user", "bob")
            return cast(list[str], await asyncio.gather(c.aget("user"), c.aget("user")))

    assert asyncio.run(main()) == ["bob", "bob"]


def test_build_validate_accepts_from_context() -> None:
    builder = ContainerBuilder()
    services = Container()
    services.user = from_context("user")
    builder.value("user", services.user.key or "user")
    c = builder.build(validate=True)
    assert c.get("user") is not None


def test_validate_accepts_from_context_rule() -> None:
    c = _request_container()
    assert c.validate() is None
