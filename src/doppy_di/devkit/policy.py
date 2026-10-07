"""Resolution order policies for container layers.

Examples:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> container = builder.build()
    >>> policy = UnorderedPolicy()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from ..container import Key, ResolveContext, RuleSetProtocol


class OrderPolicy(Protocol):
    """A strategy for controlling resolution order.

    Examples:
        >>> isinstance(UnorderedPolicy(), OrderPolicy)
        True
    """

    def before_resolve(
        self,
        _key: Key,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 protocol hook, signature fixed
        """Run before object resolution."""

    def after_resolve(
        self,
        _key: Key,
        _obj: Any,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 protocol hook, signature fixed
        """Run after object resolution."""


@dataclass(frozen=True)
class UnorderedPolicy:
    """Policy with no extra resolution ordering.

    Examples:
        >>> policy = UnorderedPolicy()
        >>> isinstance(policy, UnorderedPolicy)
        True
        >>> policy.before_resolve("x", None, ResolveContext(container))
    """

    def before_resolve(
        self,
        _key: Key,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _key, _ruleset, _ctx
        return None

    def after_resolve(
        self,
        _key: Key,
        _obj: Any,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _key, _obj, _ruleset, _ctx
        return None


@dataclass(frozen=True)
class ChildrenFirstPolicy:
    """Resolve nested children before the parent.

    Examples:
        >>> policy = ChildrenFirstPolicy(nested={"service": ["repo"]})
        >>> isinstance(policy, ChildrenFirstPolicy)
        True
        >>> policy.nested
        {'service': ['repo']}
    """

    nested: Dict[Key, List[str]]

    def __init__(self, nested: Optional[Dict[Key, List[str]]] = None) -> None:
        object.__setattr__(self, "nested", dict(nested or {}))

    def before_resolve(
        self,
        key: Key,
        _ruleset: RuleSetProtocol,
        ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _ruleset
        for child_name in self.nested.get(key, []):
            child_key = (key, child_name)
            ctx.get(child_key)

    def after_resolve(
        self,
        _key: Key,
        _obj: Any,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _key, _obj, _ruleset, _ctx
        return None


@dataclass(frozen=True)
class ParentFirstPolicy:
    """Resolve parent first, then optionally inspect children.

    Examples:
        >>> policy = ParentFirstPolicy(nested={"service": ["repo"]})
        >>> isinstance(policy, ParentFirstPolicy)
        True
    """

    nested: Dict[Key, List[str]]

    def __init__(self, nested: Optional[Dict[Key, List[str]]] = None) -> None:
        object.__setattr__(self, "nested", dict(nested or {}))

    def before_resolve(
        self,
        _key: Key,
        _ruleset: RuleSetProtocol,
        _ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _key, _ruleset, _ctx
        return None

    def after_resolve(
        self,
        key: Key,
        _obj: Any,
        _ruleset: RuleSetProtocol,
        ctx: ResolveContext,
    ) -> None:  # NOSONAR python:S1172 policy hook, signature fixed
        _ = _obj, _ruleset
        for child_name in self.nested.get(key, []):
            child_key = (key, child_name)
            ctx.get(child_key)
