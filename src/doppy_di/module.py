"""Module abstraction for grouped registration into containers.

A module is any object with a ``configure(binder)`` method (or a plain
callable accepting a binder). Modules let applications group related rules
and install them into a :class:`~doppy_di.container.ContainerBuilder` or a
:class:`~doppy_di.container.Container` — including child containers.

Examples:
    >>> from doppy_di import ContainerBuilder
    >>> class DbModule:
    ...     def configure(self, binder):
    ...         binder.value("db", "sqlite")
    >>> builder = ContainerBuilder()
    >>> builder.install(DbModule)
    >>> builder.build().get("db")
    'sqlite'
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Protocol, Tuple, Union, cast

from .container import (
    DuplicateKeyPolicy,
    DuplicateRegistrationError,
    Key,
    Lifetime,
    Rule,
    RuleSetProtocol,
)

if TYPE_CHECKING:
    from typing_extensions import Self


class Module(Protocol):
    """Protocol for module objects with a ``configure(binder)`` method."""

    def configure(self, binder: "ModuleBinder") -> None: ...


ModuleLike = Union[Module, Callable[["ModuleBinder"], None]]


class ModuleBinder:
    """Registration facade handed to modules.

    Writes go to the wrapped ruleset layer only. For a child container this
    is the local layer of a :class:`~doppy_di.container.CompositeRuleSet`,
    so a module installed on a child never mutates the parent.

    Args:
        ruleset: Target ruleset (or composite ruleset local layer).
        duplicate_policy: Policy applied when a key already exists in the
            target layer. Parent keys are never counted as duplicates for a
            child ruleset — modules may shadow parent registrations.

    Examples:
        >>> from doppy_di import ContainerBuilder
        >>> rules = ContainerBuilder().rules
        >>> binder = ModuleBinder(rules)
        >>> binder.value("y", 2)
        >>> rules.map["y"].key
        'y'
    """

    __slots__ = ("duplicate_policy", "ruleset")

    def __init__(
        self,
        ruleset: RuleSetProtocol,
        duplicate_policy: DuplicateKeyPolicy = DuplicateKeyPolicy.OVERWRITE,
    ) -> None:
        self.ruleset = ruleset
        self.duplicate_policy = duplicate_policy

    @property
    def _local_map(self) -> Dict[Key, Rule]:
        """Return the writable layer's map (own layer for composites)."""
        own = getattr(self.ruleset, "own_map", None)
        return own if own is not None else self.ruleset.map

    def _register(self, key: Key, rule: Rule) -> None:
        local = self._local_map
        existing = local.get(key)
        if existing is not None and self.duplicate_policy == DuplicateKeyPolicy.FAIL:
            raise DuplicateRegistrationError(key)
        if existing is not None and self.duplicate_policy == DuplicateKeyPolicy.WARN:
            import logging

            logging.getLogger("doppy_di.module").warning(
                "Duplicate key %r registered; overwriting", key
            )
        self.ruleset.add(key, rule)

    def service(
        self,
        key: Key,
        make: Callable[..., Any],
        lifetime: Lifetime = "transient",
        deps: "List[Key] | Tuple[Key, ...] | None" = None,
        qualifier: "str | None" = None,
        scope: "str | None" = None,
    ) -> Self:
        """Register a factory service.

        Examples:
            >>> from doppy_di import ContainerBuilder
            >>> rules = ContainerBuilder().rules
            >>> binder = ModuleBinder(rules)
            >>> binder.service("greet", lambda name: name, deps=["name"])
            >>> binder.value("name", "World")
            >>> rules.map["greet"].deps
            ('name',)
        """
        lookup = (key, qualifier) if qualifier is not None else key
        self._register(
            lookup,
            Rule(
                key=lookup,
                make=make,
                lifetime=lifetime,
                deps=tuple(deps or ()),
                scope=scope,
            ),
        )
        return self

    def value(self, key: Key, value: Any) -> Self:
        """Register a constant value as a singleton."""
        self._register(
            key,
            Rule(key=key, make=lambda: value, lifetime="singleton", deps=()),
        )
        return self

    def alias(self, key: Key, target: Key) -> Self:
        """Register an alias pointing to another key."""
        self._register(
            key,
            Rule(
                key=key,
                make=lambda value: value,
                lifetime="transient",
                deps=(target,),
            ),
        )
        return self


def apply_modules(
    ruleset: RuleSetProtocol,
    modules: "Tuple[ModuleLike, ...]",
    duplicate_policy: DuplicateKeyPolicy = DuplicateKeyPolicy.OVERWRITE,
) -> None:
    """Install modules into ``ruleset`` through a :class:`ModuleBinder`."""
    binder = ModuleBinder(ruleset, duplicate_policy)
    for module in modules:
        configure = getattr(module, "configure", None)
        if inspect.isclass(module):
            instance = cast(Any, module)()
            instance.configure(binder)
        elif configure is not None:
            configure(binder)
        else:
            cast(Callable[["ModuleBinder"], None], module)(binder)
