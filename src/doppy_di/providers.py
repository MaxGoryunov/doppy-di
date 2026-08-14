"""Declarative provider facade over :class:`~doppy_di.container.RuleSet`.

Providers are inert data objects. They carry no resolution logic and add
zero overhead until assigned to a :class:`~doppy_di.container.Container`.
Assignment converts a provider into one or more
:class:`~doppy_di.container.Rule` objects registered in the container's
rule set.

Providers are importable from ``doppy_di.providers``. The package-level
``Factory`` protocol and ``Provider`` alias in ``doppy_di.container`` are
left untouched, so this module is fully non-breaking.

Examples:
    >>> from doppy_di import Container
    >>> from doppy_di.providers import Factory, Value
    >>> services = Container()
    >>> services.config = Value({"debug": True})
    >>> services.get("config")
    {'debug': True}
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

from .container import Key, Rule, Scope

__all__ = [
    "Alias",
    "Coroutine",
    "DictOf",
    "Factory",
    "ListOf",
    "Provider",
    "Resource",
    "Scoped",
    "Selector",
    "Singleton",
    "UnboundProvider",
    "Value",
]


def _identity(value: Any) -> Any:
    """Return the argument unchanged."""
    return value


def _scope_value(scope: Union[str, Scope]) -> str:
    """Coerce a scope argument to its string name."""
    if isinstance(scope, str):
        return scope
    value = getattr(scope, "value", scope)
    if isinstance(value, str):
        return value
    return getattr(scope, "name", str(scope))


def _dep_key(dep: Union[Key, "Provider"]) -> Optional[Key]:
    """Resolve a dependency to a registration key.

    Returns ``None`` for an unbound placeholder so the dependency is dropped.
    """
    if isinstance(dep, Provider):
        if isinstance(dep, UnboundProvider):
            return None
        if dep.key is None:
            raise ValueError(
                f"Provider {dep!r} is not bound to a container; "
                "assign it first, e.g. services.x = provider"
            )
        return dep.key
    return dep


class Provider:
    """Base class for declarative providers.

    Subclasses implement :meth:`to_rules`, which converts the provider into
    one or more :class:`~doppy_di.container.Rule` objects. The ``key``
    attribute is set when the provider is assigned to a container.
    """

    key: Optional[Key] = None

    def to_rules(self, name: str) -> List[Rule]:
        """Return the rules this provider registers under ``name``."""
        raise NotImplementedError


class UnboundProvider(Provider):
    """Placeholder for a provider referenced before assignment.

    Returned by ``Container.__getattr__`` when an attribute is not yet
    assigned. Resolves to its name as a plain key when used as a dependency,
    so ``Factory(UserRepository, db=services.db)`` works before ``services.db``
    is assigned.
    """

    def __init__(self, name: str) -> None:
        self.key = name


class Factory(Provider):
    """Transient factory provider.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Factory
        >>> services = Container()
        >>> services.repo = Factory(dict)
        >>> services.get("repo")
        {}
    """

    def __init__(
        self,
        factory: Callable[..., Any],
        *dependencies: Union[Key, Provider],
        **named_dependencies: Union[Key, Provider],
    ) -> None:
        self.factory = factory
        self.dependencies = (*dependencies, *named_dependencies.values())

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(dep for dep in (_dep_key(d) for d in self.dependencies) if dep is not None)
        if isinstance(self.factory, type):
            rules = [Rule(name, self.factory, "singleton", deps)]
            rules.append(Rule(self.factory, _identity, "singleton", (name,)))
            return rules
        return [Rule(name, self.factory, "transient", deps)]


class Singleton(Provider):
    """Singleton factory provider.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Singleton
        >>> services = Container()
        >>> services.service = Singleton(list)
        >>> services.get("service") is services.get("service")
        True
    """

    def __init__(
        self,
        factory: Callable[..., Any],
        *dependencies: Union[Key, Provider],
        **named_dependencies: Union[Key, Provider],
    ) -> None:
        self.factory = factory
        self.dependencies = (*dependencies, *named_dependencies.values())

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(dep for dep in (_dep_key(d) for d in self.dependencies) if dep is not None)
        rules = [Rule(name, self.factory, "singleton", deps)]
        if isinstance(self.factory, type):
            rules.append(Rule(self.factory, _identity, "singleton", (name,)))
        return rules


class Scoped(Provider):
    """Scoped factory provider.

    The factory runs per scope; results are cached for the scope lifetime.

    Examples:
        >>> from doppy_di import Container, Scope
        >>> from doppy_di.providers import Scoped
        >>> services = Container()
        >>> services.req = Scoped(list, Scope.REQUEST)
        >>> with services.scope("req") as s:
        ...     s.get("req") is s.get("req")
        True
    """

    def __init__(
        self,
        factory: Callable[..., Any],
        scope: Union[str, Scope],
        *dependencies: Union[Key, Provider],
    ) -> None:
        self.factory = factory
        self.scope = _scope_value(scope)
        self.dependencies = dependencies

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(dep for dep in (_dep_key(d) for d in self.dependencies) if dep is not None)
        rules = [Rule(name, self.factory, "transient", deps, scope=self.scope)]
        if isinstance(self.factory, type):
            rules.append(Rule(self.factory, _identity, "transient", (name,)))
        return rules


class Value(Provider):
    """Constant value provider.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Value
        >>> services = Container()
        >>> services.config = Value({"debug": True})
        >>> services.get("config")
        {'debug': True}
    """

    def __init__(self, value: Any) -> None:
        self.value = value

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        return [Rule(name, lambda: self.value, "singleton")]


class Resource(Provider):
    """Yield-based resource provider.

    The factory must be a generator function. The resource is finalized when
    the owning scope exits.

    Examples:
        >>> from doppy_di import Container, Scope
        >>> from doppy_di.providers import Resource
        >>> def create_db():
        ...     yield "db"
        >>> services = Container()
        >>> services.db = Resource(create_db, Scope.APP)
        >>> with services.scope("app") as s:
        ...     s.get("db")
        'db'
    """

    def __init__(self, factory: Callable[..., Any], scope: Union[str, Scope]) -> None:
        self.factory = factory
        self.scope = _scope_value(scope)

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        return [Rule(name, self.factory, "singleton", scope=self.scope)]


class Coroutine(Provider):
    """Async factory provider.

    Examples:
        >>> import asyncio
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Coroutine
        >>> async def make():
        ...     return 42
        >>> services = Container()
        >>> services.answer = Coroutine(make)
        >>> asyncio.run(services.aget("answer"))
        42
    """

    def __init__(self, factory: Callable[..., Any]) -> None:
        self.factory = factory

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        return [Rule(name, self.factory, "transient")]


class Alias(Provider):
    """Alias provider pointing at another key.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Alias, Value
        >>> services = Container()
        >>> services.x = Value(1)
        >>> services.alias = Alias("x")
        >>> services.get("alias")
        1
    """

    def __init__(self, target: Union[Key, Provider]) -> None:
        self.target = target

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        target = _dep_key(self.target)
        deps = () if target is None else (target,)
        return [Rule(name, _identity, "singleton", deps)]


class Selector(Provider):
    """Provider that picks one of several providers at resolution time.

    The ``selector_fn`` receives a context object and returns the key of the
    provider to resolve.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Selector, Value
        >>> services = Container()
        >>> services.a = Value(1)
        >>> services.b = Value(2)
        >>> services.pick = Selector(
        ...     {"a": services.a, "b": services.b},
        ...     selector_fn=lambda ctx: "b",
        ... )
        >>> services.get("pick")
        2
    """

    def __init__(
        self,
        providers: Dict[str, Union[Key, Provider]],
        selector_fn: Callable[[Any], str],
    ) -> None:
        self.providers = providers
        self.selector_fn = selector_fn

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(
            dep for dep in (_dep_key(p) for p in self.providers.values()) if dep is not None
        )
        keys = list(self.providers.keys())

        def make(*args: Any) -> Any:
            idx = keys.index(self.selector_fn(None))
            return args[idx]

        return [Rule(name, make, "transient", deps)]


class ListOf(Provider):
    """Provider aggregating several providers into a list.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import ListOf, Value
        >>> services = Container()
        >>> services.a = Value(1)
        >>> services.b = Value(2)
        >>> services.all = ListOf(services.a, services.b)
        >>> services.get("all")
        [1, 2]
    """

    def __init__(self, *providers: Union[Key, Provider]) -> None:
        self.providers = providers

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(dep for dep in (_dep_key(p) for p in self.providers) if dep is not None)
        return [Rule(name, lambda *args: list(args), "transient", deps)]


class DictOf(Provider):
    """Provider aggregating named providers into a dict.

    Examples:
        >>> from doppy_di import Container
        >>> from doppy_di.providers import DictOf, Value
        >>> services = Container()
        >>> services.a = Value(1)
        >>> services.b = Value(2)
        >>> services.mapping = DictOf(a=services.a, b=services.b)
        >>> services.get("mapping")
        {'a': 1, 'b': 2}
    """

    def __init__(self, **providers: Union[Key, Provider]) -> None:
        self.providers = providers

    def to_rules(self, name: str) -> List[Rule]:
        self.key = name
        deps = tuple(
            dep for dep in (_dep_key(p) for p in self.providers.values()) if dep is not None
        )
        keys = list(self.providers.keys())
        return [Rule(name, lambda *args: dict(zip(keys, args)), "transient", deps)]
