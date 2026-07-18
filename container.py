"""Core dependency injection container.

This module provides immutable rule descriptions and a minimal container with
explicit rule registration.

Example:
    >>> builder = ContainerBuilder()
    >>> builder.service("answer", lambda: 42)
    >>> container = builder.build()
    >>> container.get("answer")
    42
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    Type,
    Union,
)

logger = logging.getLogger("doppy_di.container")


class KeyProtocol(Protocol):
    """A protocol for custom hashable keys."""

    def __hash__(self) -> int: ...

    def __eq__(self, other: object) -> bool: ...


Key = Union[str, type, KeyProtocol]
Lifetime = str


class ServiceNotFoundError(KeyError):
    """Raised when a service key is not registered.

    Example:
        >>> raise ServiceNotFoundError("missing")
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Service not found: {key!r}")


class CycleError(Exception):
    """Raised when the rule graph contains a dependency cycle.

    Example:
        >>> raise CycleError(["a", "b", "a"])
    """

    def __init__(self, path: List[Key]) -> None:
        self.path = tuple(path)
        super().__init__(f"Cycle detected: {' -> '.join(map(repr, path))}")


class NestedRuleError(Exception):
    """Raised when a nested rule validation fails.

    Example:
        >>> raise NestedRuleError("service", "db", "field mismatch")
    """

    def __init__(self, parent: Key, child: str, reason: str) -> None:
        self.parent = parent
        self.child = child
        self.reason = reason
        super().__init__(f"Nested rule error: {parent!r}.{child} - {reason}")


class ContainerBuildError(Exception):
    """Raised when build validation finds missing dependencies.

    Example:
        >>> raise ContainerBuildError([("a", "b"), ("c", "d")])
    """

    def __init__(self, missing: List[Tuple[Key, Key]]) -> None:
        self.missing = missing
        msg = "; ".join(f"{key} -> {dep}" for key, dep in missing)
        super().__init__(f"Missing dependencies: {msg}")


class DuplicateKeyError(KeyError):
    """Raised when a duplicate key is registered under the FAIL policy.

    Example:
        >>> raise DuplicateKeyError("x")
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Duplicate key: {key!r}")


class DuplicateKeyPolicy(Enum):
    """Strategy for handling duplicate key registration.

    OVERWRITE: replace existing rule (current default behavior).
    FAIL: raise DuplicateKeyError on duplicate.
    WARN: log a warning and overwrite.
    """

    OVERWRITE = "overwrite"
    FAIL = "fail"
    WARN = "warn"


class ScopePolicy(Enum):
    """Strategy for resolving scopes by name.

    NAMED: reuse the same Scope object for the same name (current default).
    UNIQUE: return a fresh Scope per call, stored under a unique internal key
            so its cache never leaks across calls even if __exit__ is forgotten.
    """

    NAMED = "named"
    UNIQUE = "unique"


class LifetimePolicy:
    """Validation policy for service lifetimes.

    Centralizes the set of known lifetime identifiers and provides an
    extension point for registering custom lifetimes.

    Example:
        >>> LifetimePolicy.validate("singleton")
        >>> LifetimePolicy.validate("transient")
        >>> LifetimePolicy.validate("per_request")  # raises ValueError
    """

    known: ClassVar[set[str]] = {"transient", "singleton"}

    @classmethod
    def validate(cls, lifetime: str) -> None:
        if lifetime not in cls.known:
            raise ValueError(f"Unknown lifetime: {lifetime!r}")


@dataclass(frozen=True)
class Rule:
    """Immutable service rule.

    Args:
        key: Registration key.
        make: Factory callable.
        lifetime: Service lifetime.
        deps: Dependency keys.

    Example:
        >>> rule = Rule("answer", lambda: 42, "singleton", ())
        >>> rule.key
        'answer'
    """

    key: Key
    make: Callable[..., Any]
    lifetime: Lifetime = "transient"
    deps: Tuple[Key, ...] = ()

    def __post_init__(self) -> None:
        LifetimePolicy.validate(self.lifetime)


class RuleSet:
    """Immutable-by-convention rule storage and dependency graph.

    Example:
        >>> rules = RuleSet()
        >>> rules.add("x", Rule("x", lambda: 1))
        >>> rules.find("x").key
        'x'
    """

    __slots__ = ("graph", "map")

    def __init__(
        self,
        rules_map: Optional[Dict[Key, Rule]] = None,
        graph: Optional[Dict[Key, Tuple[Key, ...]]] = None,
    ) -> None:
        self.map = dict(rules_map or {})
        self.graph = dict(graph or {})

    def add(self, key: Key, rule: Rule) -> None:
        """Add a rule and validate graph cycles."""
        old_map = dict(self.map)
        old_graph = dict(self.graph)
        self.map[key] = rule
        self.graph[key] = tuple(rule.deps)
        try:
            self._check_cycle(key)
        except CycleError:
            self.map = old_map
            self.graph = old_graph
            raise

    def find(self, key: Key) -> Rule:
        """Return a rule by key."""
        try:
            return self.map[key]
        except KeyError:
            raise ServiceNotFoundError(key) from None

    def has(self, key: Key) -> bool:
        """Check whether a key is registered."""
        return key in self.map

    def deps_of(self, key: Key) -> Tuple[Key, ...]:
        """Return direct dependencies for a key."""
        return self.graph.get(key, ())

    def keys(self) -> Tuple[Key, ...]:
        """Return registered keys."""
        return tuple(self.map.keys())

    def _check_cycle(self, start: Key) -> None:
        """Check graph cycles from the given start node."""
        stack: List[Key] = []
        on_stack: set[Key] = set()
        visited: set[Key] = set()

        def dfs(node: Key) -> None:
            if node in on_stack:
                raise CycleError([*stack, node])
            if node in visited:
                return
            visited.add(node)
            on_stack.add(node)
            stack.append(node)
            for dep in self.graph.get(node, ()):
                if dep in self.map:
                    dfs(dep)
            stack.pop()
            on_stack.remove(node)

        dfs(start)


class ResolveContext:
    """Resolution context used during object creation.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.service("x", lambda: 1)
        >>> c = builder.build()
        >>> ctx = ResolveContext(c)
        >>> ctx.get("x")
        1
    """

    __slots__ = ("container", "scope")

    def __init__(self, container: Container, scope: Optional[Scope] = None) -> None:
        self.container = container
        self.scope = scope or container

    def get(self, key: Key) -> Any:
        return self.scope.get(key) if isinstance(self.scope, Scope) else self.container.get(key)


class OverrideContext:
    """Context manager for temporary overrides.

    The override writes ``value`` into the container's singleton cache for
    ``key`` for the duration of the ``with`` block. On exit the previous
    state is restored via the ``had_old`` flag:

    * If ``key`` was already in the singleton cache before the override, the
      old value is restored (``had_old is True``).
    * If ``key`` was NOT yet resolved (absent from the cache), the entry is
      removed on exit (``had_old is False``). The factory then runs fresh on
      the next ``get``, producing a brand-new object rather than the one that
      was active during the override.

    This contract guarantees that overriding an unresolved singleton never
    silently mutates the eventual resolved singleton.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> c = builder.build()
        >>> with c.override("x", 2):
        ...     c.get("x")
        2
    """

    __slots__ = ("container", "had_old", "key", "old", "value")

    def __init__(self, container: Container, key: Key, value: Any) -> None:
        self.container = container
        self.key = key
        self.value = value
        self.old: Any = None
        self.had_old = False

    def __enter__(self) -> OverrideContext:
        if self.key in self.container.single:
            self.old = self.container.single[self.key]
            self.had_old = True
        self.container.single[self.key] = self.value
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self.had_old:
            self.container.single[self.key] = self.old
        else:
            self.container.single.pop(self.key, None)


class Scope:
    """Scope-local cache over a container.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.service("x", lambda: object(), lifetime="transient")
        >>> c = builder.build()
        >>> with c.scope("req") as s:
        ...     a = s.get("x")
        ...     b = s.get("x")
        ...     a is b
        True
    """

    __slots__ = ("_depth", "cache", "container", "name")

    def __init__(self, container: Container, name: str) -> None:
        self.container = container
        self.name = name
        self.cache: Dict[Key, Any] = {}
        self._depth = 0

    def get(self, key: Key) -> Any:
        if key in self.cache:
            return self.cache[key]
        obj = self.container.get(key)
        self.cache[key] = obj
        return obj

    def __enter__(self) -> Scope:
        self._depth += 1
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._depth -= 1
        if self._depth == 0:
            self.cache.clear()


class Container:
    """Runtime container with singleton cache.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.service("answer", lambda: 42, lifetime="singleton")
        >>> container = builder.build()
        >>> container.get("answer")
        42
    """

    __slots__ = ("config", "lock", "scope_policy", "scopes", "single")

    def __init__(self, config: ContainerConfig) -> None:
        self.config = config
        self.single: Dict[Key, Any] = {}
        self.scopes: Dict[str, Scope] = {}
        self.scope_policy: ScopePolicy = config.scope_policy
        self.lock = threading.RLock()

    def get(self, key: Key) -> Any:
        if key in self.single:
            return self.single[key]

        with self.lock:
            if key in self.single:
                return self.single[key]

            try:
                rule = self.config.ruleset.find(key)
            except ServiceNotFoundError:
                raise
            ctx = ResolveContext(self)
            args = [ctx.get(dep) for dep in rule.deps]
            obj = rule.make(*args)

            if rule.lifetime == "singleton":
                self.single[key] = obj
            self._cache_nested_aliases(key, obj)

            return obj

    def _cache_nested_aliases(self, key: Key, obj: Any) -> None:
        for alias in self.config.ruleset.map:
            if not isinstance(alias, tuple) or len(alias) != 2 or alias[0] != key:
                continue
            child = alias[1]
            if isinstance(child, str) and hasattr(obj, child):
                self.single[alias] = getattr(obj, child)

    def has(self, key: Key) -> bool:
        return self.config.ruleset.has(key)

    def get_or_none(self, key: Key) -> Any:
        """Return resolved service or ``None`` if key not registered.

        Example:
            >>> builder = ContainerBuilder()
            >>> c = builder.build()
            >>> c.get_or_none("missing") is None
            True
        """
        try:
            return self.get(key)
        except ServiceNotFoundError:
            return None

    def scope(self, name: str) -> Scope:
        if self.scope_policy == ScopePolicy.NAMED:
            if name in self.scopes:
                return self.scopes[name]
            scope = Scope(self, name)
            self.scopes[name] = scope
            return scope
        # UNIQUE: fresh Scope per call, stored under unique internal key
        internal = f"{name}#{uuid.uuid4().hex}"
        scope = Scope(self, name)
        self.scopes[internal] = scope
        return scope

    def override(self, key: Key, value: Any) -> OverrideContext:
        return OverrideContext(self, key, value)


@dataclass(frozen=True)
class ContainerConfig:
    """Immutable container configuration.

    Example:
        >>> rules = RuleSet()
        >>> config = ContainerConfig(rules)
        >>> isinstance(config.ruleset, RuleSet)
        True
    """

    ruleset: RuleSet
    scope_policy: ScopePolicy = ScopePolicy.NAMED


class ContainerBuilder:
    """Builder for a container.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> c = builder.build()
        >>> c.get("x")
        1
    """

    __slots__ = ("duplicate_policy", "rules", "scope_policy")

    def __init__(
        self,
        duplicate_policy: DuplicateKeyPolicy = DuplicateKeyPolicy.OVERWRITE,
        scope_policy: ScopePolicy = ScopePolicy.NAMED,
    ) -> None:
        self.rules = RuleSet()
        self.duplicate_policy = duplicate_policy
        self.scope_policy = scope_policy

    def _register(self, key: Key, rule: Rule) -> None:
        """Add rule honoring the active duplicate policy."""
        if key in self.rules.map and self.duplicate_policy != DuplicateKeyPolicy.OVERWRITE:
            if self.duplicate_policy == DuplicateKeyPolicy.FAIL:
                raise DuplicateKeyError(key)
            # WARN
            logger.warning("Duplicate key %r registered; overwriting", key)
        self.rules.add(key, rule)

    def service(
        self,
        key: Key,
        make: Callable[..., Any],
        lifetime: Lifetime = "transient",
        deps: Optional[List[Key]] = None,
    ) -> None:
        rule = Rule(key=key, make=make, lifetime=lifetime, deps=tuple(deps or ()))
        self._register(key, rule)

    def value(self, key: Key, value: Any) -> None:
        def make_value() -> Any:
            return value

        self._register(
            key,
            Rule(key=key, make=make_value, lifetime="singleton", deps=()),
        )

    def alias(self, key: Key, target: Key) -> None:
        def make_alias(value: Any) -> Any:
            return value

        self._register(
            key,
            Rule(
                key=key,
                make=make_alias,
                lifetime="transient",
                deps=(target,),
            ),
        )

    def build(self, validate: bool = False) -> Container:
        if validate:
            missing: List[Tuple[Key, Key]] = []
            for key, rule in self.rules.map.items():
                for dep in rule.deps:
                    if dep not in self.rules.map:
                        missing.append((key, dep))
            if missing:
                raise ContainerBuildError(missing)
        return Container(ContainerConfig(self.rules, scope_policy=self.scope_policy))
