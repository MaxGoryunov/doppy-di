"""Nested-rule validation helpers.

Examples:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("service", object())
    >>> nested = NestedRules()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Protocol

from ..container import Container, Key, NestedRuleError, Rule, RuleSetProtocol

logger = logging.getLogger(__name__)


class NestedPolicy(Protocol):
    """Policy used to compare nested objects.

    Implementations compare the resolved nested attribute against the object
    stored under the nested rule key.

    Examples:
        >>> isinstance(SameObjectPolicy(), NestedPolicy)
        True
    """

    def check(self, nested: Any, resolved: Any) -> bool:
        """Return True if nested object is valid."""


@dataclass(frozen=True)
class SameObjectPolicy:
    """Check object identity for nested values.

    Examples:
        >>> policy = SameObjectPolicy()
        >>> obj = object()
        >>> policy.check(obj, obj)
        True
    """

    def check(self, nested: Any, resolved: Any) -> bool:
        """Return True only when both references are the same object."""
        return nested is resolved


@dataclass(frozen=True)
class SameValuePolicy:
    """Check value equality for nested values.

    Attributes:
        strict: When True, propagate comparison exceptions instead of
            returning False.

    Examples:
        >>> policy = SameValuePolicy()
        >>> policy.check(1, 1)
        True
        >>> policy.check(1, 2)
        False
    """

    strict: bool = False

    def check(self, nested: Any, resolved: Any) -> bool:
        """Return True when both values compare equal.

        When ``strict`` is False, exceptions raised during comparison are
        logged and treated as inequality.
        """
        try:
            return bool(nested == resolved)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning(
                "SameValuePolicy check failed for %r == %r: %s",
                nested,
                resolved,
                exc,
            )
            return False


@dataclass(frozen=True)
class NestedEntry:
    """Describe one nested registration.

    Examples:
        >>> entry = NestedEntry("service", "repo")
        >>> entry.parent
        'service'
        >>> entry.child
        'repo'
    """

    parent: Key
    child: str


class NestedRules:
    """Track nested relations and validate resolved objects."""

    __slots__ = ("map", "same_policy")

    def __init__(self) -> None:
        self.map: Dict[Key, List[str]] = {}
        self.same_policy: NestedPolicy = SameValuePolicy()

    def add_nested(self, parent: Key, child: str, rule: Rule, ruleset: RuleSetProtocol) -> None:
        """Register a nested dependency for a parent key.

        The nested key ``(parent, child)`` is added to the shared ruleset and
        tracked for validation.

        Examples:
            >>> nested = NestedRules()
            >>> rule = Rule(("db", "conn"), lambda: object())
            >>> rs = RuleSet()
            >>> nested.add_nested("db", "conn", rule, rs)
            >>> rs.has(("db", "conn"))
            True
        """
        nested_key = (parent, child)
        ruleset.add(nested_key, replace(rule, nested=True))
        self.map.setdefault(parent, [])
        if child not in self.map[parent]:
            self.map[parent].append(child)

    def children_of(self, parent: Key) -> List[str]:
        """Return the registered nested child names for a parent."""
        return list(self.map.get(parent, []))

    def validate_nested(self, parent: Key, container: Container, parent_obj: Any = None) -> None:
        """Validate nested rules for a resolved parent object."""
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
