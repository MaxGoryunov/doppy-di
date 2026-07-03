"""Nested-rule validation helpers.

Example:
    >>> from container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> nested = NestedRules()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol

from container import Container, Key, NestedRuleError, Rule, RuleSet


class NestedPolicy(Protocol):
    """Policy used to compare nested objects."""

    def check(self, nested: Any, resolved: Any) -> bool:
        """Return True if nested object is valid."""


@dataclass(frozen=True)
class SameObjectPolicy:
    """Check object identity for nested values.

    Example:
        >>> policy = SameObjectPolicy()
        >>> policy.check(object(), object())
        False
    """

    def check(self, nested: Any, resolved: Any) -> bool:
        return nested is resolved


@dataclass(frozen=True)
class SameValuePolicy:
    """Check value equality for nested values.

    Example:
        >>> policy = SameValuePolicy()
        >>> policy.check(1, 1)
        True
    """

    def check(self, nested: Any, resolved: Any) -> bool:
        return bool(nested == resolved)


@dataclass(frozen=True)
class NestedEntry:
    """Describe one nested registration.

    Example:
        >>> entry = NestedEntry("service", "repo")
        >>> entry.parent
        'service'
    """

    parent: Key
    child: str


class NestedRules:
    """Track nested relations and validate resolved objects.

    Example:
        >>> nested = NestedRules()
        >>> rule = Rule(("service", "repo"), lambda repo: repo)
        >>> nested.add_nested("service", "repo", rule, RuleSet())
    """

    __slots__ = ("map", "same_policy")

    def __init__(self) -> None:
        self.map: Dict[Key, List[str]] = {}
        self.same_policy: NestedPolicy = SameValuePolicy()

    def add_nested(self, parent: Key, child: str, rule: Rule, ruleset: RuleSet) -> None:
        nested_key = (parent, child)
        ruleset.add(nested_key, rule)
        self.map.setdefault(parent, [])
        if child not in self.map[parent]:
            self.map[parent].append(child)

    def children_of(self, parent: Key) -> List[str]:
        return list(self.map.get(parent, []))

    def validate_nested(self, parent: Key, container: Container, parent_obj: Any = None) -> None:
        children = self.children_of(parent)
        if not children:
            return

        if parent_obj is None:
            parent_obj = container.get(parent)
        for child in children:
            nested_key = (parent, child)
            nested_obj = container.get(nested_key)

            if not hasattr(parent_obj, child):
                raise NestedRuleError(parent, child, f"field {child!r} not found")

            resolved = getattr(parent_obj, child)
            if not self.same_policy.check(nested_obj, resolved):
                raise NestedRuleError(parent, child, "nested validation failed")
