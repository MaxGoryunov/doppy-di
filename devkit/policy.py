"""Resolution order policies for container layers.

Example:
    >>> from container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> container = builder.build()
    >>> policy = UnorderedPolicy()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from container import Key, ResolveContext, RuleSet


class OrderPolicy(Protocol):
    """A strategy for controlling resolution order."""

    def before_resolve(self, key: Key, ruleset: RuleSet, ctx: ResolveContext) -> None:
        """Run before object resolution."""

    def after_resolve(self, key: Key, obj: Any, ruleset: RuleSet, ctx: ResolveContext) -> None:
        """Run after object resolution."""


@dataclass(frozen=True)
class UnorderedPolicy:
    """Policy with no extra resolution ordering.

    Example:
        >>> policy = UnorderedPolicy()
        >>> isinstance(policy, UnorderedPolicy)
        True
    """

    def before_resolve(self, key: Key, ruleset: RuleSet, ctx: ResolveContext) -> None:
        return None

    def after_resolve(self, key: Key, obj: Any, ruleset: RuleSet, ctx: ResolveContext) -> None:
        return None


@dataclass(frozen=True)
class ChildrenFirstPolicy:
    """Resolve nested children before the parent.

    Example:
        >>> policy = ChildrenFirstPolicy(nested={"service": ["repo"]})
        >>> isinstance(policy, ChildrenFirstPolicy)
        True
    """

    nested: Dict[Key, List[str]]

    def __init__(self, nested: Optional[Dict[Key, List[str]]] = None) -> None:
        object.__setattr__(self, "nested", dict(nested or {}))

    def before_resolve(self, key: Key, ruleset: RuleSet, ctx: ResolveContext) -> None:
        for child_name in self.nested.get(key, []):
            child_key = (key, child_name)
            ctx.get(child_key)

    def after_resolve(self, key: Key, obj: Any, ruleset: RuleSet, ctx: ResolveContext) -> None:
        return None


@dataclass(frozen=True)
class ParentFirstPolicy:
    """Resolve parent first, then optionally inspect children.

    Example:
        >>> policy = ParentFirstPolicy(nested={"service": ["repo"]})
        >>> isinstance(policy, ParentFirstPolicy)
        True
    """

    nested: Dict[Key, List[str]]

    def __init__(self, nested: Optional[Dict[Key, List[str]]] = None) -> None:
        object.__setattr__(self, "nested", dict(nested or {}))

    def before_resolve(self, key: Key, ruleset: RuleSet, ctx: ResolveContext) -> None:
        return None

    def after_resolve(self, key: Key, obj: Any, ruleset: RuleSet, ctx: ResolveContext) -> None:
        for child_name in self.nested.get(key, []):
            child_key = (key, child_name)
            ctx.get(child_key)
