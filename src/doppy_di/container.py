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

import asyncio
import inspect
import logging
import threading
import uuid
from contextlib import (
    AsyncExitStack,
    ExitStack,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass
from enum import Enum
from types import ModuleType, TracebackType
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    ParamSpec,
    Protocol,
    Tuple,
    Type,
    TypeVar,
    Union,
    runtime_checkable,
)

from typing_extensions import Self, TypeAlias

logger = logging.getLogger("doppy_di.container")


class KeyProtocol(Protocol):
    """A protocol for custom hashable keys.

    Example:
        >>> class MyKey:
        ...     def __hash__(self): return 1
        ...     def __eq__(self, other): return isinstance(other, MyKey)
        >>> isinstance(MyKey(), KeyProtocol)
        True
    """

    def __hash__(self) -> int: ...

    def __eq__(self, other: object) -> bool: ...


Key = Union[str, type, KeyProtocol, Tuple[Any, str]]
Lifetime = str

P = ParamSpec("P")
T = TypeVar("T", covariant=True)


@runtime_checkable
class Factory(Protocol[P, T]):
    """A factory callable with parameter specification.

    Example:
        >>> def make(host: str) -> Database:
        ...     return Database(host)
        >>> isinstance(make, Factory)
        True
    """

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T: ...


Provider: TypeAlias = Callable[P, T]


@dataclass(frozen=True)
class Qualifier:
    """Marker for named dependencies via ``typing.Annotated``.

    Example:
        >>> Qualifier("read")
        Qualifier(name='read')
    """

    name: str


class ServiceNotFoundError(KeyError):
    """Raised when a service key is not registered.

    Example:
        >>> raise ServiceNotFoundError("missing")
        Traceback (most recent call last):
        ...
        ServiceNotFoundError: Service not found: 'missing'
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Service not found: {key!r}")


class UnregisteredTypeError(KeyError):
    """Raised when an override targets an unregistered key.

    Example:
        >>> raise UnregisteredTypeError("missing")
        Traceback (most recent call last):
        ...
        UnregisteredTypeError: Unregistered type: 'missing'
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Unregistered type: {key!r}")


class CycleError(Exception):
    """Raised when the rule graph contains a dependency cycle.

    Example:
        >>> raise CycleError(["a", "b", "a"])
        Traceback (most recent call last):
        ...
        CycleError: Cycle detected: 'a' -> 'b' -> 'a'
    """

    def __init__(self, path: List[Key]) -> None:
        self.path = tuple(path)
        super().__init__(f"Cycle detected: {' -> '.join(map(repr, path))}")


class YieldNotCalledError(Exception):
    """Raised when a yield provider generator does not yield.

    Example:
        >>> raise YieldNotCalledError("session")
        Traceback (most recent call last):
        ...
        YieldNotCalledError: Yield provider 'session' did not yield a value
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Yield provider {key!r} did not yield a value")


class NestedRuleError(Exception):
    """Raised when a nested rule validation fails.

    Example:
        >>> raise NestedRuleError("service", "db", "field mismatch")
        Traceback (most recent call last):
        ...
        NestedRuleError: Nested rule error: 'service'.db - field mismatch
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
        Traceback (most recent call last):
        ...
        ContainerBuildError: Missing dependencies: a -> b; c -> d
    """

    def __init__(self, missing: List[Tuple[Key, Key]]) -> None:
        self.missing = missing
        msg = "; ".join(f"{key} -> {dep}" for key, dep in missing)
        super().__init__(f"Missing dependencies: {msg}")


class ValidationError(Exception):
    """Base class for static graph validation errors.

    Example:
        >>> raise ValidationError("bad graph")
        Traceback (most recent call last):
        ...
        ValidationError: bad graph
    """


class UnregisteredDependencyError(ValidationError):
    """Raised when a rule depends on an unregistered key.

    Example:
        >>> raise UnregisteredDependencyError("a", "b")
        Traceback (most recent call last):
        ...
        UnregisteredDependencyError: Unregistered dependency: 'a' -> 'b'
    """

    def __init__(self, key: Key, dependency: Key) -> None:
        self.key = key
        self.dependency = dependency
        super().__init__(f"Unregistered dependency: {key!r} -> {dependency!r}")


class CyclicDependencyError(ValidationError):
    """Raised when the rule graph contains a dependency cycle.

    Example:
        >>> raise CyclicDependencyError(["a", "b", "a"])
        Traceback (most recent call last):
        ...
        CyclicDependencyError: Cycle detected: 'a' -> 'b' -> 'a'
    """

    def __init__(self, path: List[Key]) -> None:
        self.path = tuple(path)
        super().__init__(f"Cycle detected: {' -> '.join(map(repr, path))}")


class InvalidFactoryError(ValidationError):
    """Raised when a factory is incompatible with its declared deps.

    Example:
        >>> raise InvalidFactoryError("a", "arity mismatch")
        Traceback (most recent call last):
        ...
        InvalidFactoryError: Invalid factory for 'a': arity mismatch
    """

    def __init__(self, key: Key, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"Invalid factory for {key!r}: {reason}")


class DuplicateKeyError(KeyError):
    """Raised when a duplicate key is registered under the FAIL policy.

    Example:
        >>> raise DuplicateKeyError("x")
        Traceback (most recent call last):
        ...
        DuplicateKeyError: Duplicate key: 'x'
    """

    def __init__(self, key: Key) -> None:
        self.key = key
        super().__init__(f"Duplicate key: {key!r}")


class DuplicateKeyPolicy(Enum):
    """Strategy for handling duplicate key registration.

    OVERWRITE: replace existing rule (current default behavior).
    FAIL: raise DuplicateKeyError on duplicate.
    WARN: log a warning and overwrite.

    Example:
        >>> DuplicateKeyPolicy.FAIL.name
        'FAIL'
        >>> DuplicateKeyPolicy.OVERWRITE.value
        'overwrite'
    """

    OVERWRITE = "overwrite"
    FAIL = "fail"
    WARN = "warn"


class ScopePolicy(Enum):
    """Strategy for resolving scopes by name.

    NAMED: reuse the same Scope object for the same name (current default).
    UNIQUE: return a fresh Scope per call, stored under a unique internal
            key so its cache never leaks across calls even if __exit__
            is forgotten.

    Example:
        >>> ScopePolicy.NAMED.value
        'named'
        >>> ScopePolicy.UNIQUE.name
        'UNIQUE'
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
        Traceback (most recent call last):
        ...
        ValueError: Unknown lifetime: 'per_request'
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
        >>> rule.lifetime
        'singleton'
    """

    key: Key
    make: Callable[..., Any]
    lifetime: Lifetime = "transient"
    deps: Tuple[Key, ...] = ()
    yield_provider: bool = False
    async_yield_provider: bool = False
    nested: bool = False
    scope: Optional[str] = None

    def __post_init__(self) -> None:
        LifetimePolicy.validate(self.lifetime)
        if inspect.isasyncgenfunction(self.make):
            object.__setattr__(self, "async_yield_provider", True)
        elif inspect.isgeneratorfunction(self.make):
            object.__setattr__(self, "yield_provider", True)


class RuleSet:
    """Immutable-by-convention rule storage and dependency graph.

    Example:
        >>> rules = RuleSet()
        >>> rules.add("x", Rule("x", lambda: 1))
        >>> rules.find("x").key
        'x'
        >>> rules.has("x")
        True
    """

    __slots__ = ("graph", "map", "version")

    def __init__(
        self,
        rules_map: Optional[Dict[Key, Rule]] = None,
        graph: Optional[Dict[Key, Tuple[Key, ...]]] = None,
    ) -> None:
        """Initialize storage from optional existing map and graph."""
        self.map = dict(rules_map or {})
        self.graph = dict(graph or {})
        self.version = 0

    def add(self, key: Key, rule: Rule) -> None:
        """Add a rule and validate graph cycles.

        Raises:
            CycleError: If adding the rule creates a dependency cycle.

        Example:
            >>> rules = RuleSet()
            >>> rules.add("a", Rule("a", lambda: 1, deps=("b",)))
            >>> rules.has("a")
            True
        """
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
        self.version += 1

    def find(self, key: Key) -> Rule:
        """Return a rule by key.

        Raises:
            ServiceNotFoundError: If key is not registered.

        Example:
            >>> rules = RuleSet()
            >>> rules.add("x", Rule("x", lambda: 1))
            >>> rules.find("x")
            Rule(key='x', make=..., lifetime='transient', deps=())
            >>> rules.find("missing")  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            ...
            ServiceNotFoundError
        """
        try:
            return self.map[key]
        except KeyError:
            raise ServiceNotFoundError(key) from None

    def has(self, key: Key) -> bool:
        """Check whether a key is registered.

        Example:
            >>> rules = RuleSet()
            >>> rules.add("x", Rule("x", lambda: 1))
            >>> rules.has("x")
            True
            >>> rules.has("y")
            False
        """
        return key in self.map

    def deps_of(self, key: Key) -> Tuple[Key, ...]:
        """Return direct dependencies for a key.

        Example:
            >>> rules = RuleSet()
            >>> rules.add("a", Rule("a", lambda: 1, deps=("b", "c")))
            >>> rules.deps_of("a")
            ('b', 'c')
        """
        return self.graph.get(key, ())

    def keys(self) -> Tuple[Key, ...]:
        """Return registered keys.

        Example:
            >>> rules = RuleSet()
            >>> rules.add("x", Rule("x", lambda: 1))
            >>> rules.add("y", Rule("y", lambda: 2))
            >>> sorted(rules.keys())
            ['x', 'y']
        """
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

    Provides access to the container and scope for dependency resolution.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.service("x", lambda: 1)
        >>> c = builder.build()
        >>> ctx = ResolveContext(c)
        >>> ctx.get("x")
        1
    """

    __slots__ = ("container", "scope")

    def __init__(
        self,
        container: Container,
        scope: Optional[Scope] = None,
    ) -> None:
        self.container = container
        self.scope = scope or container

    def get(self, key: Key) -> Any:
        if isinstance(self.scope, Scope):
            return self.scope.get(key)
        return self.container.get(key)


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
        >>> c.get("x")
        1
    """

    __slots__ = ("container", "had_old", "key", "old", "value")

    def __init__(self, container: Container, key: Key, value: Any) -> None:
        self.container = container
        self.key = key
        self.value = value
        self.old: Any = None
        self.had_old = False

    def __enter__(self) -> OverrideContext:
        if self.key not in self.container.config.ruleset.map:
            raise UnregisteredTypeError(self.key)
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

    Scopes allow caching within a ``with`` block. All resolved values are
    cached until the scope exits.

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

    __slots__ = (
        "_async_exit_stack",
        "_depth",
        "_exit_stack",
        "cache",
        "container",
        "name",
    )

    def __init__(self, container: Container, name: str) -> None:
        """Create a named scope with an empty local cache."""
        self.container = container
        self.name = name
        self.cache: Dict[Key, Any] = {}
        self._exit_stack: List[ExitStack] = []
        self._async_exit_stack: List[AsyncExitStack] = []
        self._depth = 0

    def get(self, key: Key) -> Any:
        """Resolve key from scope cache or underlying container.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("x", 1)
            >>> c = builder.build()
            >>> with c.scope("s") as s:
            ...     s.get("x")
            1
        """
        if key in self.cache:
            return self.cache[key]
        rule = self.container.config.ruleset.find(key)
        if rule.yield_provider:
            stack = ExitStack()
            try:
                obj = stack.enter_context(contextmanager(rule.make)())
            except RuntimeError as exc:
                if "didn't yield" in str(exc):
                    raise YieldNotCalledError(key) from None
                raise
            self._exit_stack.append(stack)
            self.cache[key] = obj
            return obj
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
            for stack in self._exit_stack:
                try:
                    stack.close()
                except Exception:
                    logger.exception("Error finalizing yield provider")
            self._exit_stack.clear()


class AsyncScope(Scope):
    """Async scope-local cache with async yield provider support.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> c = builder.build()
        >>> async def main():
        ...     async with c.ascope("s") as s:
        ...         return await s.get("x")
        >>> import asyncio
        >>> asyncio.run(main())
        1
    """

    async def get(self, key: Key) -> Any:
        """Resolve key from scope cache or underlying container."""
        if key in self.cache:
            return self.cache[key]
        rule = self.container.config.ruleset.find(key)
        if rule.async_yield_provider:
            stack = AsyncExitStack()
            try:
                obj = await stack.enter_async_context(asynccontextmanager(rule.make)())
            except RuntimeError as exc:
                if "didn't yield" in str(exc):
                    raise YieldNotCalledError(key) from None
                raise
            self._async_exit_stack.append(stack)
            self.cache[key] = obj
            return obj
        if rule.yield_provider:
            raise TypeError(f"Sync yield provider {key!r} cannot be resolved in async scope")
        obj = self.container.get(key)
        self.cache[key] = obj
        return obj

    async def __aenter__(self) -> AsyncScope:
        self._depth += 1
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._depth -= 1
        if self._depth == 0:
            self.cache.clear()
            for stack in self._async_exit_stack:
                try:
                    await stack.aclose()
                except Exception:
                    logger.exception("Error finalizing yield provider")
            self._async_exit_stack.clear()


class Container:
    """Runtime container with singleton cache.

    Thread-safe singleton resolution with double-checked locking.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.service("answer", lambda: 42, lifetime="singleton")
        >>> container = builder.build()
        >>> container.get("answer")
        42
    """

    __slots__ = (
        "_visualize_cache",
        "_visualize_version",
        "config",
        "lock",
        "scope_policy",
        "scopes",
        "single",
    )

    def __init__(self, config: ContainerConfig) -> None:
        self.config = config
        self.single: Dict[Key, Any] = {}
        self.scopes: Dict[str, Scope] = {}
        self.scope_policy: ScopePolicy = config.scope_policy
        self.lock = threading.RLock()
        self._visualize_cache: Dict[str, Any] = {}
        self._visualize_version = -1

    def get(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Resolve a service by key.

        Returns the cached singleton if already resolved, otherwise resolves
        the rule from the config, applies the scope policy, and stores the
        result for singleton lifetimes.

        Double-checked locking provides thread safety.

        Args:
            key: Service key.
            qualifier: Optional named qualifier. When given, resolves the
                rule registered as ``(key, qualifier)``.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.service("answer", lambda: 42)
            >>> container = builder.build()
            >>> container.get("answer")
            42
        """
        lookup = (key, qualifier) if qualifier is not None else key
        if lookup in self.single:
            return self.single[lookup]

        with self.lock:
            if lookup in self.single:
                return self.single[lookup]

            try:
                rule = self.config.ruleset.find(lookup)
            except ServiceNotFoundError:
                if self._is_injectable_key(lookup):
                    from .auto_wiring import _rule_for

                    self.config.ruleset.add(lookup, _rule_for(lookup))
                    rule = self.config.ruleset.find(lookup)
                elif qualifier is not None:
                    raise UnregisteredDependencyError(key, qualifier) from None
                else:
                    raise
            if rule.async_yield_provider:
                raise TypeError(f"Async yield provider {lookup!r} requires async scope")
            ctx = ResolveContext(self)
            try:
                args = [ctx.get(dep) for dep in rule.deps]
            except ServiceNotFoundError as exc:
                if self._is_injectable_key(lookup):
                    from .auto_wiring import UnresolvableDependencyError

                    raise UnresolvableDependencyError(lookup, exc.key) from None
                raise
            obj = rule.make(*args)

            if rule.lifetime == "singleton":
                self.single[lookup] = obj
            self._cache_nested_aliases(lookup, obj)

            return obj

    @staticmethod
    def _is_injectable_key(key: Key) -> bool:
        """Return True when key is an injectable type or qualified type."""
        if isinstance(key, type):
            return bool(getattr(key, "__doppy_injectable__", False))
        if isinstance(key, tuple) and len(key) == 2 and isinstance(key[0], type):
            return bool(getattr(key[0], "__doppy_injectable__", False))
        return False

    def _cache_nested_aliases(self, key: Key, obj: Any) -> None:
        for alias, rule in self.config.ruleset.map.items():
            if not rule.nested:
                continue
            if not isinstance(alias, tuple) or len(alias) != 2 or alias[0] != key:
                continue
            child = alias[1]
            if isinstance(child, str) and hasattr(obj, child):
                self.single[alias] = getattr(obj, child)

    async def aget(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Resolve a service by key asynchronously.

        Resolves dependencies concurrently with ``asyncio.gather`` and awaits
        async factories. Sync factories are called directly, so there is no
        overhead for sync dependencies. Singleton results are cached.

        Args:
            key: Service key.
            qualifier: Optional named qualifier. When given, the rule
                registered as ``(key, qualifier)`` is resolved.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.service("answer", lambda: 42)
            >>> container = builder.build()
            >>> async def main():
            ...     return await container.aget("answer")
            >>> import asyncio
            >>> asyncio.run(main())
            42
        """
        lookup = (key, qualifier) if qualifier is not None else key
        if lookup in self.single:
            return self.single[lookup]

        try:
            rule = self.config.ruleset.find(lookup)
        except ServiceNotFoundError:
            if self._is_injectable_key(lookup):
                from .auto_wiring import _rule_for

                self.config.ruleset.add(lookup, _rule_for(lookup))
                rule = self.config.ruleset.find(lookup)
            elif qualifier is not None:
                raise UnregisteredDependencyError(key, qualifier) from None
            else:
                raise
        if rule.async_yield_provider:
            raise TypeError(f"Async yield provider {lookup!r} requires async scope")
        args = await asyncio.gather(*(self.aget(dep) for dep in rule.deps))
        obj = rule.make(*args)
        if inspect.isawaitable(obj):
            obj = await obj

        if rule.lifetime == "singleton":
            self.single[lookup] = obj
        self._cache_nested_aliases(lookup, obj)

        return obj

    async def get_many(self, keys: List[Key], parallel: bool = False) -> List[Any]:
        """Resolve multiple services, optionally in parallel.

        Args:
            keys: Service keys to resolve.
            parallel: When True, resolve independent dependencies
                concurrently. Falls back to sequential resolution for small
                graphs (fewer than 5 nodes) where parallelism overhead
                outweighs the benefit.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("a", 1)
            >>> builder.value("b", 2)
            >>> container = builder.build()
            >>> async def main():
            ...     return await container.get_many(["a", "b"])
            >>> import asyncio
            >>> asyncio.run(main())
            [1, 2]
        """
        if not parallel:
            return [await self.aget(key) for key in keys]

        levels = self._independent_levels(keys)
        total = sum(len(level) for level in levels)
        if total < 5:
            return [await self.aget(key) for key in keys]

        results: Dict[Key, Any] = {}
        for level in levels:
            resolved = await asyncio.gather(*(self.aget(key) for key in level))
            results.update(dict(zip(level, resolved)))
        return [results[key] for key in keys]

    def _independent_levels(self, keys: List[Key]) -> List[List[Key]]:
        """Group keys and their transitive deps into dependency levels.

        Each level contains nodes whose dependencies all appear in earlier
        levels, so nodes within a level can be resolved concurrently.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("a", 1)
            >>> builder.service("b", lambda a: a + 1, deps=["a"])
            >>> container = builder.build()
            >>> container._independent_levels(["b"])
            [['a'], ['b']]
        """
        ruleset = self.config.ruleset
        needed: set[Key] = set()
        stack = list(keys)
        while stack:
            key = stack.pop()
            if key in needed:
                continue
            needed.add(key)
            stack.extend(ruleset.deps_of(key))

        indegree: Dict[Key, int] = dict.fromkeys(needed, 0)
        dependents: Dict[Key, List[Key]] = {key: [] for key in needed}
        for key in needed:
            for dep in ruleset.deps_of(key):
                if dep in needed:
                    indegree[key] += 1
                    dependents[dep].append(key)

        ready = [key for key in needed if indegree[key] == 0]
        levels: List[List[Key]] = []
        while ready:
            levels.append(ready)
            next_ready: List[Key] = []
            for key in ready:
                for dependent in dependents[key]:
                    indegree[dependent] -= 1
                    if indegree[dependent] == 0:
                        next_ready.append(dependent)
            ready = next_ready
        return levels

    def scan(
        self,
        *packages: Union[ModuleType, str],
        recursive: bool = True,
    ) -> None:
        """Register all injectable classes found in the given packages.

        Explicitly registered rules are never overridden.

        Example:
            >>> builder = ContainerBuilder()
            >>> container = builder.build()
            >>> container.scan(__name__)
        """
        from .auto_wiring import scan_package

        for pkg in packages:
            scan_package(self, pkg, recursive)

    def has(self, key: Key, qualifier: Optional[str] = None) -> bool:
        """Return True if a rule for key is registered.

        Args:
            key: Service key.
            qualifier: Optional named qualifier.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.service("x", lambda: 1)
            >>> c = builder.build()
            >>> c.has("x")
            True
            >>> c.has("missing")
            False
        """
        lookup = (key, qualifier) if qualifier is not None else key
        return self.config.ruleset.has(lookup)

    def get_or_none(self, key: Key, qualifier: Optional[str] = None) -> Any:
        """Return resolved service or ``None`` if key not registered.

        Args:
            key: Service key.
            qualifier: Optional named qualifier.

        Example:
            >>> builder = ContainerBuilder()
            >>> c = builder.build()
            >>> c.get_or_none("missing") is None
            True
        """
        try:
            return self.get(key, qualifier=qualifier)
        except (ServiceNotFoundError, UnregisteredDependencyError):
            return None

    def scope(self, name: str) -> Scope:
        """Return a named or unique scope according to the active policy.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("x", 1)
            >>> c = builder.build()
            >>> s = c.scope("req")
            >>> isinstance(s, Scope)
            True
        """
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

    def ascope(self, name: str) -> AsyncScope:
        """Return a named or unique async scope.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("x", 1)
            >>> c = builder.build()
            >>> s = c.ascope("req")
            >>> isinstance(s, AsyncScope)
            True
        """
        if self.scope_policy == ScopePolicy.NAMED:
            if name in self.scopes:
                existing = self.scopes[name]
                if isinstance(existing, AsyncScope):
                    return existing
                raise TypeError(f"Scope {name!r} already exists as sync scope")
            scope = AsyncScope(self, name)
            self.scopes[name] = scope
            return scope
        # UNIQUE: fresh Scope per call, stored under unique internal key
        internal = f"{name}#{uuid.uuid4().hex}"
        scope = AsyncScope(self, name)
        self.scopes[internal] = scope
        return scope

    def override(self, key: Key, value: Any) -> OverrideContext:
        """Create a temporary override context for the given key.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("x", 1)
            >>> c = builder.build()
            >>> ctx = c.override("x", 2)
            >>> isinstance(ctx, OverrideContext)
            True
        """
        return OverrideContext(self, key, value)

    def visualize(self, format: str = "mermaid") -> Any:  # noqa: A002
        """Return a textual representation of the dependency graph.

        Supported formats:
            - ``"mermaid"`` — mermaid ``graph TD`` for embedding in Markdown.
            - ``"graphviz"`` — Graphviz ``digraph`` for PNG/SVG generation.
            - ``"json"`` — structured dict for programmatic processing.

        Rendering applies only to registered rules. Lifetime and optional
        scope are encoded as node color and shape. Edges participating in a
        cycle are marked ``[CYCLE]``. The result is cached until the rule set
        changes, so repeated calls are free.

        Args:
            format: Output format among ``"mermaid"``, ``"graphviz"``,
                ``"json"``.

        Returns:
            str for ``"mermaid"``/``"graphviz"``, dict for ``"json"``.

        Raises:
            ValueError: If ``format`` is not supported.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("db", object())
            >>> builder.service("service", lambda db: db, deps=["db"])
            >>> c = builder.build()
            >>> out = c.visualize()
            >>> "graph TD" in out
            True
        """
        from .devkit.visualize import render

        if self.config.ruleset.version != self._visualize_version:
            self._visualize_cache = {}
            self._visualize_version = self.config.ruleset.version
        if format not in self._visualize_cache:
            self._visualize_cache[format] = render(self.config.ruleset, format)
        return self._visualize_cache[format]

    def validate(
        self,
        strict: bool = True,
    ) -> Optional[List[ValidationError]]:
        """Validate the whole dependency graph statically.

        Checks every registered rule for missing dependencies, dependency
        cycles, and factory arity mismatches. Validation is explicit and
        never runs automatically, so there is zero overhead unless called.

        Args:
            strict: When True, raise ValidationError on the first error.
                When False, collect all errors and return them as a list.

        Returns:
            None when strict=True and the graph is valid.
            List of ValidationError when strict=False.

        Example:
            >>> builder = ContainerBuilder()
            >>> builder.value("x", 1)
            >>> c = builder.build()
            >>> c.validate() is None
            True
        """
        errors: List[ValidationError] = []
        ruleset = self.config.ruleset

        for key, rule in ruleset.map.items():
            for dep in rule.deps:
                if dep not in ruleset.map:
                    errors.append(UnregisteredDependencyError(key, dep))

            try:
                sig = inspect.signature(rule.make)
            except (TypeError, ValueError):
                sig = None
            if sig is not None:
                positional = [
                    p
                    for p in sig.parameters.values()
                    if p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                required = sum(1 for p in positional if p.default is inspect.Parameter.empty)
                total = len(positional)
                has_varargs = any(
                    p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
                )
                if len(rule.deps) < required:
                    errors.append(
                        InvalidFactoryError(
                            key,
                            f"factory requires at least {required} args "
                            f"but only {len(rule.deps)} deps declared",
                        )
                    )
                elif len(rule.deps) > total and not has_varargs:
                    errors.append(
                        InvalidFactoryError(
                            key,
                            f"factory accepts at most {total} args "
                            f"but {len(rule.deps)} deps declared",
                        )
                    )

        for key in ruleset.map:
            try:
                ruleset._check_cycle(key)
            except CycleError as exc:
                errors.append(CyclicDependencyError(list(exc.path)))

        if strict:
            if errors:
                raise errors[0]
            return None
        return errors


@dataclass(frozen=True)
class ContainerConfig:
    """Immutable container configuration.

    Example:
        >>> rules = RuleSet()
        >>> config = ContainerConfig(rules)
        >>> isinstance(config.ruleset, RuleSet)
        True
        >>> config.scope_policy
        <ScopePolicy.NAMED: 'named'>
    """

    ruleset: RuleSet
    scope_policy: ScopePolicy = ScopePolicy.NAMED


class ContainerBuilder:
    """Builder for a container.

    Provides methods to register services, values, and aliases, then
    produce a ready-to-use Container.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> builder.service("y", lambda x: x + 1, deps=["x"])
        >>> c = builder.build()
        >>> c.get("y")
        2
    """

    __slots__ = ("duplicate_policy", "rules", "scope_policy")

    def __init__(
        self,
        duplicate_policy: DuplicateKeyPolicy = DuplicateKeyPolicy.OVERWRITE,
        scope_policy: ScopePolicy = ScopePolicy.NAMED,
    ) -> None:
        """Initialize builder with optional policies.

        Example:
            >>> b = ContainerBuilder(
            ...     DuplicateKeyPolicy.FAIL, ScopePolicy.UNIQUE
            ... )
            >>> b.duplicate_policy
            <DuplicateKeyPolicy.FAIL: 'fail'>
        """
        self.rules = RuleSet()
        self.duplicate_policy = duplicate_policy
        self.scope_policy = scope_policy

    def _register(self, key: Key, rule: Rule) -> None:
        """Add rule honoring the active duplicate policy."""
        if key in self.rules.map and (self.duplicate_policy != DuplicateKeyPolicy.OVERWRITE):
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
        qualifier: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Self:
        """Register a factory service.

        Args:
            key: Registration key.
            make: Factory callable.
            lifetime: Service lifetime.
            deps: Dependency keys.
            qualifier: Optional named qualifier. When given, the rule is
                stored under the key ``(key, qualifier)``.

        Example:
            >>> b = ContainerBuilder()
            >>> b.service("greet", lambda name: f"Hello {name}", deps=["name"])
            >>> b.value("name", "World")
            >>> c = b.build()
            >>> c.get("greet")
            'Hello World'
        """
        lookup = (key, qualifier) if qualifier is not None else key
        rule = Rule(
            key=lookup,
            make=make,
            lifetime=lifetime,
            deps=tuple(deps or ()),
            scope=scope,
        )
        self._register(lookup, rule)
        return self

    def value(self, key: Key, value: Any) -> Self:
        """Register a constant value as a singleton.

        Example:
            >>> b = ContainerBuilder()
            >>> b.value("pi", 3.14)
            >>> c = b.build()
            >>> c.get("pi")
            3.14
        """

        def make_value() -> Any:
            return value

        self._register(
            key,
            Rule(
                key=key,
                make=make_value,
                lifetime="singleton",
                deps=(),
            ),
        )
        return self

    def alias(self, key: Key, target: Key) -> Self:
        """Register an alias pointing to another key.

        Example:
            >>> b = ContainerBuilder()
            >>> b.value("x", 42)
            >>> b.alias("answer", "x")
            >>> c = b.build()
            >>> c.get("answer")
            42
        """

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
        return self

    def build(self, validate: bool = False) -> Container:
        """Build and return a Container.

        Args:
            validate: When True, raises ContainerBuildError if any
                dependency key is not registered.

        Example:
            >>> b = ContainerBuilder()
            >>> b.value("x", 1)
            >>> c = b.build()
            >>> c.get("x")
            1

        Example with validation:
            >>> b = ContainerBuilder()
            >>> b.service("a", lambda b: b, deps=["b"])
            >>> b.build(validate=True)  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            ...
            ContainerBuildError
        """
        if validate:
            missing: List[Tuple[Key, Key]] = []
            for key, rule in self.rules.map.items():
                for dep in rule.deps:
                    if dep not in self.rules.map:
                        missing.append((key, dep))
            if missing:
                raise ContainerBuildError(missing)
        return Container(ContainerConfig(self.rules, scope_policy=self.scope_policy))
