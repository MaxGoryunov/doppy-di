"""Multibinding: implicit collections, SetOf, Selector context."""

import asyncio
from typing import Dict, List, Set

import pytest

from doppy_di import Container
from doppy_di.container import Rule, ServiceNotFoundError
from doppy_di.providers import (
    DictOf,
    ListOf,
    Selector,
    SelectorContext,
    SetOf,
    Value,
)


class Iface:
    pass


def _register(container: Container, key: object, value: object) -> None:
    container.config.ruleset.add(key, Rule(key, lambda v=value: v, "singleton"))


class TestImplicitList:
    def test_collects_all_implementations_in_insertion_order(self) -> None:
        services = Container()
        _register(services, Iface, "base")
        _register(services, (Iface, "a"), "first")
        _register(services, (Iface, "b"), "second")
        assert services.get(List[Iface]) == ["base", "first", "second"]

    def test_insertion_order_is_deterministic(self) -> None:
        services = Container()
        _register(services, (Iface, "b"), 2)
        _register(services, (Iface, "a"), 1)
        assert services.get(List[Iface]) == [2, 1]

    def test_no_members_raises(self) -> None:
        services = Container()
        with pytest.raises(ServiceNotFoundError):
            services.get(List[Iface])

    def test_explicit_registration_wins(self) -> None:
        services = Container()
        _register(services, (Iface, "a"), "member")
        services.config.ruleset.add(
            List[Iface], Rule(List[Iface], lambda: ["explicit"], "singleton")
        )
        assert services.get(List[Iface]) == ["explicit"]

    def test_usable_as_nested_dependency(self) -> None:
        services = Container()
        _register(services, (Iface, "a"), 1)
        _register(services, (Iface, "b"), 2)
        services.config.ruleset.add(
            "consumer", Rule("consumer", lambda items: items, "singleton", (List[Iface],))
        )
        assert services.get("consumer") == [1, 2]


class TestImplicitSet:
    def test_collects_members(self) -> None:
        services = Container()
        _register(services, Iface, "base")
        _register(services, (Iface, "a"), "x")
        assert services.get(Set[Iface]) == {"base", "x"}

    def test_no_members_raises(self) -> None:
        services = Container()
        with pytest.raises(ServiceNotFoundError):
            services.get(Set[Iface])


class TestImplicitDict:
    def test_qualified_keys_become_dict_entries(self) -> None:
        services = Container()
        _register(services, (Iface, "read"), "r")
        _register(services, (Iface, "write"), "w")
        assert services.get(Dict[str, Iface]) == {"read": "r", "write": "w"}

    def test_base_key_uses_class_name(self) -> None:
        services = Container()
        _register(services, Iface, "base")
        assert services.get(Dict[str, Iface]) == {"Iface": "base"}

    def test_no_members_raises(self) -> None:
        services = Container()
        with pytest.raises(ServiceNotFoundError):
            services.get(Dict[str, Iface])


class TestAsync:
    def test_aget_resolves_collection(self) -> None:
        services = Container()
        _register(services, (Iface, "a"), 1)
        _register(services, (Iface, "b"), 2)
        assert asyncio.run(services.aget(List[Iface])) == [1, 2]

    def test_nested_async_dependency(self) -> None:
        services = Container()
        _register(services, (Iface, "a"), 1)
        services.config.ruleset.add(
            "consumer", Rule("consumer", lambda items: items, "singleton", (List[Iface],))
        )
        assert asyncio.run(services.aget("consumer")) == [1]


class TestSetOf:
    def test_setof_collects_providers(self) -> None:
        services = Container()
        services.a = Value(1)
        services.b = Value(2)
        services.all = SetOf(services.a, services.b)
        assert services.get("all") == {1, 2}

    def test_setof_rejects_unbound(self) -> None:
        services = Container()
        with pytest.raises(ValueError, match="Unbound provider"):
            services.all = SetOf(services.missing)


class TestAggregateUnbound:
    def test_listof_rejects_unbound(self) -> None:
        services = Container()
        with pytest.raises(ValueError, match="Unbound provider"):
            services.all = ListOf(services.missing)

    def test_dictof_rejects_unbound(self) -> None:
        services = Container()
        with pytest.raises(ValueError, match="Unbound provider"):
            services.all = DictOf(a=services.missing)


class TestSelectorContext:
    def test_selector_fn_receives_real_context(self) -> None:
        services = Container()
        services.a = Value(1)
        services.b = Value(2)
        seen: Dict[str, object] = {}

        def pick(ctx: SelectorContext) -> str:
            seen["key"] = ctx.key
            seen["providers"] = ctx.providers
            return "b"

        services.pick = Selector({"a": services.a, "b": services.b}, selector_fn=pick)
        assert services.get("pick") == 2
        assert seen["key"] == "pick"
        assert seen["providers"] == {"a": "a", "b": "b"}

    def test_selector_rejects_unbound(self) -> None:
        services = Container()
        with pytest.raises(ValueError, match="Unbound provider"):
            services.pick = Selector({"x": services.missing}, selector_fn=lambda ctx: "x")


class TestDuplicatePolicy:
    def test_re_registering_member_after_get_refreshes_collection(self) -> None:
        services = Container()
        _register(services, (Iface, "a"), 1)
        assert services.get(List[Iface]) == [1]
        _register(services, (Iface, "b"), 2)
        assert services.get(List[Iface]) == [1, 2]
