"""Validation layer for container resolution.

Examples:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> container = builder.build()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from ..container import (
    Container,
    CycleError,
    Key,
    OverrideContext,
    ResolveContext,
    Scope,
)
from .nested import NestedRules
from .policy import OrderPolicy


class ValidationRule(Protocol):
    """A validation rule executed after resolution.

    Examples:
        >>> class MyRule:
        ...     def check(self, container, key, obj):
        ...         assert obj is not None
        >>> isinstance(MyRule(), ValidationRule)
        True
    """

    def check(self, container: Container, key: Key, obj: Any) -> None:
        """Validate resolved object."""


@dataclass(frozen=True)
class ValidationRunner:
    """Run a list of validation rules.

    Examples:
        >>> runner = ValidationRunner()
        >>> len(runner.rules)
        0
        >>> runner.add(MyRule())
        >>> len(runner.rules)
        1
    """

    rules: tuple[ValidationRule, ...]

    def __init__(self, rules: Optional[List[ValidationRule]] = None) -> None:
        object.__setattr__(self, "rules", tuple(rules or ()))

    def add(self, rule: ValidationRule) -> None:
        """Append a validation rule.

        Examples:
            >>> runner = ValidationRunner()
            >>> runner.add(MyRule())
            >>> len(runner.rules)
            1
        """
        object.__setattr__(self, "rules", (*self.rules, rule))

    def run(self, container: Container, key: Key, obj: Any) -> None:
        """Execute all registered rules.

        Examples:
            >>> runner = ValidationRunner()
            >>> runner.run(container, "x", 42)
        """
        for rule in self.rules:
            rule.check(container, key, obj)


class ValidatingContainer:
    """Container view that applies order policy and validation.

    Wraps a base Container with resolution ordering and optional
    validation rules.

    Examples:
        >>> from doppy_di.container import ContainerBuilder
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> base = builder.build()
        >>> wrapped = ValidatingContainer(
        ...     base, UnorderedPolicy(), ValidationRunner()
        ... )
        >>> wrapped.get("x")
        1
    """

    __slots__ = ("_resolving", "nested", "policy", "validator", "wrapped")

    def __init__(
        self,
        wrapped: Container,
        policy: OrderPolicy,
        validator: Optional[ValidationRunner] = None,
        nested: Optional[NestedRules] = None,
    ) -> None:
        """Wrap container with validation and resolution guard."""
        self.wrapped = wrapped
        self.policy = policy
        self.validator = validator or ValidationRunner()
        self.nested = nested
        self._resolving: set[Key] = set()

    def get(self, key: Key) -> Any:
        """Resolve key with ordering and validation.

        Examples:
            >>> wrapped = ValidatingContainer(base, UnorderedPolicy())
            >>> wrapped.get("x")
            1
        """
        if key in self._resolving:
            raise CycleError([key])
        self._resolving.add(key)
        try:
            ctx = ResolveContext(self.wrapped)
            ruleset = self.wrapped.config.ruleset

            self.policy.before_resolve(key, ruleset, ctx)
            obj = self.wrapped.get(key)
            self.policy.after_resolve(key, obj, ruleset, ctx)

            self.validator.run(self.wrapped, key, obj)

            if self.nested is not None and key in self.nested.map:
                self.nested.validate_nested(key, self.wrapped, obj)

            return obj
        finally:
            self._resolving.remove(key)

    def has(self, key: Key) -> bool:
        """Check if key is registered.

        Examples:
            >>> wrapped.has("x")
            True
        """
        return self.wrapped.has(key)

    def scope(self, name: str) -> Scope:
        """Return a scope from the wrapped container.

        Examples:
            >>> s = wrapped.scope("req")
            >>> isinstance(s, Scope)
            True
        """
        return self.wrapped.scope(name)

    def override(self, key: Key, value: Any) -> OverrideContext:
        """Override a value in the wrapped container.

        Examples:
            >>> with wrapped.override("x", 2):
            ...     wrapped.get("x")
            2
        """
        return self.wrapped.override(key, value)
