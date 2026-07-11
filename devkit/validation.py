"""Validation layer for container resolution.

Example:
    >>> from container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> container = builder.build()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from container import Container, Key, OverrideContext, ResolveContext, Scope

from .nested import NestedRules
from .policy import OrderPolicy


class ValidationRule(Protocol):
    """A validation rule executed after resolution."""

    def check(self, container: Container, key: Key, obj: Any) -> None:
        """Validate resolved object."""


@dataclass(frozen=True)
class ValidationRunner:
    """Run a list of validation rules.

    Example:
        >>> runner = ValidationRunner()
        >>> len(runner.rules)
        0
    """

    rules: tuple[ValidationRule, ...]

    def __init__(self, rules: Optional[List[ValidationRule]] = None) -> None:
        object.__setattr__(self, "rules", tuple(rules or ()))

    def add(self, rule: ValidationRule) -> None:
        object.__setattr__(self, "rules", (*self.rules, rule))

    def run(self, container: Container, key: Key, obj: Any) -> None:
        for rule in self.rules:
            rule.check(container, key, obj)


class ValidatingContainer:
    """Container view that applies order policy and validation.

    Example:
        >>> from container import ContainerBuilder
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> base = builder.build()
        >>> wrapped = ValidatingContainer(base, UnorderedPolicy(), ValidationRunner())
        >>> wrapped.get("x")
        1
    """

    __slots__ = ("nested", "policy", "validator", "wrapped")

    def __init__(
        self,
        wrapped: Container,
        policy: OrderPolicy,
        validator: Optional[ValidationRunner] = None,
        nested: Optional[NestedRules] = None,
    ) -> None:
        self.wrapped = wrapped
        self.policy = policy
        self.validator = validator or ValidationRunner()
        self.nested = nested

    def get(self, key: Key) -> Any:
        ctx = ResolveContext(self.wrapped)
        ruleset = self.wrapped.config.ruleset

        self.policy.before_resolve(key, ruleset, ctx)
        obj = self.wrapped.get(key)
        self.policy.after_resolve(key, obj, ruleset, ctx)

        self.validator.run(self.wrapped, key, obj)

        if self.nested is not None and key in self.nested.map:
            self.nested.validate_nested(key, self.wrapped, obj)

        return obj

    def has(self, key: Key) -> bool:
        return self.wrapped.has(key)

    def scope(self, name: str) -> Scope:
        return self.wrapped.scope(name)

    def override(self, key: Key, value: Any) -> OverrideContext:
        return self.wrapped.override(key, value)
