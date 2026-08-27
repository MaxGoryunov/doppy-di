"""Tests for the guardless compiled-plan fast path (issue #120)."""

from __future__ import annotations

from typing import Any

import pytest

from doppy_di import ContainerBuilder, ServiceNotFoundError


class Settings:
    pass


class ApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class UserRepository:
    def __init__(self, client: ApiClient) -> None:
        self.client = client


class EmailSender:
    def __init__(self, client: ApiClient) -> None:
        self.client = client


class AuditLog:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class RegisterUser:
    def __init__(
        self,
        repo: UserRepository,
        email: EmailSender,
        audit: AuditLog,
    ) -> None:
        self.repo = repo
        self.email = email
        self.audit = audit


def _build_container() -> Any:
    builder = ContainerBuilder()
    builder.value(Settings, Settings())
    builder.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    builder.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    builder.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    builder.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    builder.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )
    return builder.build()


def test_guardless_plan_is_opt_in() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    assert plan.guardless is True


def test_guardless_default_is_off() -> None:
    container = _build_container()
    plan = container.compile()
    assert plan.guardless is False


def test_guardless_resolves_graph_without_container_delegation() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)

    class _BoomMap(dict[object, object]):
        def __getitem__(self, key: Any) -> Any:
            raise AssertionError("guardless plan must not delegate to container.get()")

    original_map: dict[object, object] = container.config.ruleset.map
    container.config.ruleset.map = _BoomMap(original_map)  # type: ignore[assignment]
    try:
        obj = plan.get(RegisterUser)
    finally:
        container.config.ruleset.map = original_map  # type: ignore[assignment]

    assert isinstance(obj, RegisterUser)
    assert isinstance(obj.repo, UserRepository)
    assert isinstance(obj.email, EmailSender)
    assert isinstance(obj.audit, AuditLog)
    assert isinstance(obj.repo.client, ApiClient)
    assert isinstance(obj.repo.client.settings, Settings)


def test_guardless_singleton_identity() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    a = plan.get(ApiClient)
    b = plan.get(ApiClient)
    assert a is b


def test_guardless_transient_fresh() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    assert plan.get(UserRepository) is not plan.get(UserRepository)


def test_guardless_shared_singleton_across_transients() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    obj = plan.get(RegisterUser)
    assert obj.repo.client.settings is obj.audit.settings
    assert obj.repo.client is obj.email.client


def test_guardless_bound_resolver_single_call() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    bound = plan.bind(RegisterUser)
    assert callable(bound)
    assert isinstance(bound(), RegisterUser)
    assert bound.kind in {"flat", "frozen"}


def test_guardless_bound_unknown_key_raises() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    with pytest.raises(ServiceNotFoundError):
        plan.bind("missing")


def test_guardless_qualifier_key() -> None:
    builder = ContainerBuilder()
    builder.service("k", lambda: 1, qualifier="q", lifetime="singleton")
    plan = builder.build().compile(guardless=True)
    assert plan.get("k", "q") == 1
    bound = plan.bind("k", "q")
    assert bound() == 1


def test_guardless_unknown_key_raises() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    with pytest.raises(ServiceNotFoundError):
        plan.get("nope")


def test_guardless_post_compile_override_disallowed() -> None:
    container = _build_container()
    plan = container.compile(guardless=True)
    with pytest.raises(RuntimeError), container.override(ApiClient, object()):
        plan.get(ApiClient)


def test_regular_plan_keeps_override_visibility() -> None:
    """Non-guardless plan must still honor overrides (issue #120 regression guard)."""
    container = _build_container()
    plan = container.compile()
    marker = ApiClient(Settings())
    with container.override(ApiClient, marker):
        assert plan.get(ApiClient) is marker


def test_regular_plan_keeps_tracer() -> None:
    container = _build_container()
    plan = container.compile()
    events: list[Any] = []

    def tracer(*args: Any) -> None:
        events.append(args)

    container.set_tracer(tracer)
    try:
        plan.get(ApiClient)
    finally:
        container.set_tracer(None)
    assert events


def test_guardless_ineligible_async_key_falls_back() -> None:
    import asyncio

    async def make_async() -> int:
        return 7

    builder = ContainerBuilder()
    builder.service("a", make_async)
    container = builder.build()
    plan = container.compile(guardless=True)
    assert asyncio.run(plan.aget("a")) == 7


def test_guardless_nested_alias_direct() -> None:
    from doppy_di import Rule

    builder = ContainerBuilder()
    builder.value("child", 1)
    builder.rules.add(
        ("parent", "child"),
        Rule(
            ("parent", "child"),
            lambda child: child,
            lifetime="transient",
            deps=("child",),
            nested=True,
        ),
    )
    plan = builder.build().compile(guardless=True)
    assert plan.get(("parent", "child")) == 1
