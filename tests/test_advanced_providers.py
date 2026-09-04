"""Advanced provider types: Dependency, Selector runtime context.

Covers issue #53: a required ``Dependency`` provider, runtime-keyed
``Selector`` choice and label-to-key robustness of ``SelectorContext``.
"""

from __future__ import annotations

import pytest

from doppy_di import Container, Scope
from doppy_di.container import ServiceNotFoundError
from doppy_di.providers import (
    Dependency,
    Factory,
    Selector,
    SelectorContext,
    Value,
    from_context,
)


class UserRepository:
    pass


class TestDependency:
    def test_resolves_present_target_by_key(self) -> None:
        services = Container()
        services.target = Value(1)
        services.req = Dependency("target")

        assert services.get("req") == 1

    def test_resolves_present_target_by_provider(self) -> None:
        services = Container()
        services.target = Value(1)
        services.req = Dependency(services.target)

        assert services.get("req") == 1

    def test_resolves_to_singleton_target(self) -> None:
        services = Container()
        services.target = Value(1)
        services.req = Dependency("target")

        assert services.get("req") is services.get("req")

    def test_resolves_class_registration(self) -> None:
        services = Container()
        repo = Factory(UserRepository)
        services.repo = repo
        services.req = Dependency(UserRepository)

        assert isinstance(services.get("req"), UserRepository)

    def test_missing_key_raises_at_resolution(self) -> None:
        services = Container()
        services.req = Dependency("nope")

        with pytest.raises(ServiceNotFoundError):
            services.get("req")

    def test_rejects_unbound_provider(self) -> None:
        services = Container()
        with pytest.raises(ValueError, match="Unbound provider"):
            services.req = Dependency(services.missing)


class TestSelectorRuntimeContext:
    def test_context_carries_resolved_runtime_value(self) -> None:
        services = Container()
        services.a = Value(1)
        services.b = Value(2)
        services.env = from_context("env", Scope.REQUEST)

        def pick(ctx: SelectorContext) -> str:
            return "b" if ctx.context == "prod" else "a"

        services.pick = Selector(
            {"a": services.a, "b": services.b},
            selector_fn=pick,
            context=services.env,
        )

        with services.scope("req") as s:
            s.set_context("env", "prod")
            assert s.get("pick") == 2
        with services.scope("req") as s:
            s.set_context("env", "dev")
            assert s.get("pick") == 1

    def test_context_is_none_when_omitted(self) -> None:
        services = Container()
        services.a = Value(1)
        services.pick = Selector(
            {"a": services.a},
            selector_fn=lambda ctx: "a",
        )
        assert services.get("pick") == 1

    def test_unknown_label_raises_clear_error(self) -> None:
        services = Container()
        services.a = Value(1)
        services.pick = Selector(
            {"a": services.a},
            selector_fn=lambda ctx: "missing",
        )

        with pytest.raises(ValueError, match="label"):
            services.get("pick")

    def test_label_decoupled_from_registered_key(self) -> None:
        services = Container()
        services.a = Value(1)
        services.pick = Selector(
            {"alias": services.a},
            selector_fn=lambda ctx: "alias",
        )

        assert services.get("pick") == 1
