"""Type-based auto-wiring support.

Provides the ``@injectable`` decorator and helpers used by ``Container.scan()``
and lazy registration in ``Container.get()``.

Examples:
    >>> from doppy_di.container import ContainerBuilder
    >>> @injectable
    ... class Service:
    ...     pass
    >>> builder = ContainerBuilder()
    >>> container = builder.build()
    >>> container.get(Service)
    <...Service object at ...>
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType
from typing import Any, List, Optional, Tuple, Type, TypeGuard, get_args, get_origin

from .container import Key, Qualifier, Rule


class MissingAnnotationError(TypeError):
    """Raised when an injectable class has an unannotated dependency."""

    def __init__(self, cls: type, param: str) -> None:
        self.cls = cls
        self.param = param
        super().__init__(f"Missing annotation for {param!r} in {cls!r}")


class UnresolvableDependencyError(Exception):
    """Raised when an auto-wired dependency cannot be resolved."""

    def __init__(self, key: Key, dep: Key) -> None:
        self.key = key
        self.dep = dep
        super().__init__(f"Unresolvable dependency: {key!r} -> {dep!r}")


_INJECTABLE_FLAG = "__doppy_injectable__"
_INJECTABLE_META = "__doppy_injectable_meta__"


def is_injectable(cls: type) -> TypeGuard[type]:
    """Return True when ``cls`` is marked with ``@injectable``.

    Examples:
        >>> @injectable
        ... class Service:
        ...     pass
        >>> is_injectable(Service)
        True
        >>> is_injectable(int)
        False
    """
    return bool(getattr(cls, _INJECTABLE_FLAG, False))


def injectable(
    cls: Optional[Type[Any]] = None,
    *,
    scope: Optional[str] = None,
    qualifier: Optional[str] = None,
) -> Any:
    """Mark a class as a candidate for auto-registration.

    Usable bare (``@injectable``) or with options
    (``@injectable(scope=..., qualifier=...)``).

    Args:
        cls: Class being decorated (bare usage).
        scope: Default lifetime for the class.
        qualifier: Named qualifier.

    Examples:
        >>> @injectable(scope="singleton")
        ... class Service:
        ...     pass
        >>> Service.__doppy_injectable__
        True
    """

    def decorate(target: Type[Any]) -> Type[Any]:
        setattr(target, _INJECTABLE_FLAG, True)
        setattr(
            target,
            _INJECTABLE_META,
            {"scope": scope, "qualifier": qualifier},
        )
        return target

    if cls is not None:
        return decorate(cls)
    return decorate


def _annotation_key(annotation: Any) -> Any:
    """Convert an annotation into a container lookup key.

    Unwraps ``Annotated[Type, Qualifier("read")]`` into the tuple key
    ``(Type, "read")``. Plain annotations map to themselves.
    """
    if get_origin(annotation) is not None and hasattr(annotation, "__metadata__"):
        for meta in getattr(annotation, "__metadata__", ()):
            if isinstance(meta, Qualifier):
                return (get_args(annotation)[0], meta.name)
    return annotation


def _deps_of(cls: type) -> Tuple[Key, ...]:
    """Extract annotated constructor dependencies for a class.

    Raises:
        MissingAnnotationError: If a parameter lacks an annotation.
    """
    if inspect.signature(cls) == inspect.signature(object):
        return ()
    sig = inspect.signature(cls)
    deps: list[Key] = []
    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if param.annotation is inspect.Parameter.empty:
            raise MissingAnnotationError(cls, name)
        deps.append(_annotation_key(param.annotation))
    return tuple(deps)


def _rule_for(cls: Key) -> Rule:
    """Build a resolution rule for an injectable class.

    When ``cls`` is a ``(type, qualifier)`` tuple, the rule is stored under
    the qualified key. Otherwise the class meta qualifier is used.
    """
    qualifier: Optional[str] = None
    target: type = cls  # type: ignore[assignment]
    if isinstance(cls, tuple) and len(cls) == 2:
        qualifier = cls[1]
        target = cls[0]
    meta = getattr(target, _INJECTABLE_META, {}) or {}
    scope = meta.get("scope")
    qualifier = qualifier or meta.get("qualifier")
    key: Key = (target, qualifier) if qualifier else target
    return Rule(
        key=key,
        make=target,
        lifetime=scope or "singleton",
        deps=_deps_of(target),
    )


def _iter_modules(pkg: ModuleType, recursive: bool) -> Any:
    """Yield modules to scan for injectable classes."""
    if not recursive or not hasattr(pkg, "__path__"):
        yield pkg
        return
    prefix = pkg.__name__ + "."
    for info in pkgutil.walk_packages(pkg.__path__, prefix):
        yield importlib.import_module(info.name)


def scan_package(
    container: Any,
    pkg: ModuleType | str,
    recursive: bool = True,
) -> None:
    """Register all injectable classes found in a package.

    Explicitly registered rules are never overridden.
    """
    module = importlib.import_module(pkg) if isinstance(pkg, str) else pkg
    for mod in _iter_modules(module, recursive):
        for obj in vars(mod).values():
            if not (isinstance(obj, type) and getattr(obj, _INJECTABLE_FLAG, False)):
                continue
            if container.config.ruleset.has(obj):
                continue
            container.config.ruleset.add(obj, _rule_for(obj))


_BUILTIN_TYPES: Tuple[type, ...] = (int, str, bool, float, bytes, dict, list, set, tuple)


class TypeFallbackStrategy:
    """Configurable type-based fallback for unregistered dependencies.

    When no explicit registration exists, the container may attempt to
    construct the dependency from its type annotation. Default behavior is
    off: explicitly pass a strategy to enable implicit construction.

    Args:
        allow_concrete_classes: Resolve concrete classes (default True).
        allow_protocols: Resolve Protocol types when unambiguous (default False).
        allow_builtins: Resolve builtin types (default False, discouraged).
        max_depth: Maximum implicit resolution chain depth.

    Explicit rules always win over fallback. Builtins, ``Any``, unions
    without rules, and ambiguous protocols stay rejected by default.
    Cycles use existing detection and report the full type chain.

    Examples:
        >>> from doppy_di.container import ContainerBuilder
        >>> from doppy_di.auto_wiring import TypeFallbackStrategy
        >>> class Repo: pass
        >>> from doppy_di import injectable
        >>> @injectable
        ... class Service:
        ...     def __init__(self, repo: Repo) -> None:
        ...         self.repo = repo
        >>> c = ContainerBuilder(autowiring_strategy=TypeFallbackStrategy()).build()
        >>> isinstance(c.get(Service).repo, Repo)
        True
    """

    def __init__(
        self,
        allow_concrete_classes: bool = True,
        allow_protocols: bool = False,
        allow_builtins: bool = False,
        max_depth: int = 16,
    ) -> None:
        self.allow_concrete_classes = allow_concrete_classes
        self.allow_protocols = allow_protocols
        self.allow_builtins = allow_builtins
        self.max_depth = max_depth

    def _reject_alias(self, target: Any, origin: Any) -> Optional[str]:
        """Reject generic alias, unwrap Annotated, recurse origin."""
        import typing

        args = get_args(target)
        if origin is typing.Union or "Union" in str(origin):
            return f"union {target!r} needs an explicit rule"
        if hasattr(target, "__metadata__"):
            inner = args[0] if args else None
            if inner is None:
                return f"empty annotation {target!r} needs an explicit rule"
            return self.should_attempt(inner)
        if isinstance(origin, type):
            return self.should_attempt(origin)
        return f"generic {target!r} needs an explicit rule"

    def _reject_type(self, target: type) -> Optional[str]:
        """Reject builtin, protocol, abstract, non-injectable."""
        if not self.allow_builtins and target in _BUILTIN_TYPES:
            return f"builtin {target!r} is blocked by default"
        if getattr(target, "_is_protocol", False):
            if self.allow_protocols:
                return None
            return f"protocol {target!r} needs allow_protocols=True"
        if inspect.isabstract(target) and not self.allow_protocols:
            return f"abstract {target!r} is ambiguous without an explicit rule"
        if not self.allow_concrete_classes and not is_injectable(target):
            return f"{target!r} is not marked @injectable (STRICT mode)"
        return None

    def should_attempt(self, target: Any) -> Optional[str]:
        """Return None when allowed, else human-readable rejection reason."""
        if target is None or target is Any:
            return "Any or missing annotation cannot be resolved implicitly"
        origin = get_origin(target)
        if origin is not None:
            return self._reject_alias(target, origin)
        if not isinstance(target, type):
            return f"{target!r} is not a resolvable type"
        return self._reject_type(target)

    def check_depth(self, depth: int) -> Optional[str]:
        """Return None when depth ok, else rejection reason."""
        if depth > self.max_depth:
            return f"max_depth {self.max_depth} exceeded"
        return None


def _fallback_deps(target: type) -> Tuple[Key, ...]:
    """Resolve constructor deps handling string annotations."""
    import typing

    fn = getattr(target, "__init__", None)
    hints = typing.get_type_hints(fn, include_extras=True) if fn is not None else {}
    if not hints:
        try:
            hints = typing.get_type_hints(target, include_extras=True)
        except Exception:
            hints = {}
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return ()
    deps: list[Key] = []
    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if name in hints:
            deps.append(_annotation_key(hints[name]))
        elif param.annotation is not inspect.Parameter.empty:
            deps.append(_annotation_key(param.annotation))
        else:
            raise MissingAnnotationError(target, name)
    return tuple(deps)


def _target_of_lookup(lookup: Key) -> Any:
    """Return the type behind a lookup key, else None."""
    if isinstance(lookup, type):
        return lookup
    if isinstance(lookup, tuple) and len(lookup) == 2 and isinstance(lookup[0], type):
        return lookup[0]
    return None


def _fallback_rule_for(lookup: Key, strategy: TypeFallbackStrategy, path: List[Key]) -> Rule:
    """Build implicit rule for ``lookup`` or raise descriptive error."""
    target = _target_of_lookup(lookup)
    owner: Key = path[-1] if path else lookup
    reason = strategy.check_depth(len(path))
    if reason is None:
        reason = strategy.should_attempt(target) if target is not None else None
    if reason is None and target is None:
        reason = f"{lookup!r} is not a resolvable type"
    if reason is not None:
        raise UnresolvableDependencyError(owner, lookup)
    assert target is not None
    try:
        deps = _fallback_deps(target)
    except MissingAnnotationError as exc:
        raise UnresolvableDependencyError(owner, lookup) from exc
    meta = getattr(target, _INJECTABLE_META, {}) or {}
    key: Key = target
    if isinstance(lookup, tuple) and len(lookup) == 2:
        key = (target, lookup[1])
    elif meta.get("qualifier"):
        key = (target, meta.get("qualifier"))
    return Rule(
        key=key,
        make=target,
        lifetime=meta.get("scope") or "singleton",
        deps=deps,
    )


def _patch_builder_for_fallback() -> None:
    """No-op hook kept for backward compatibility (fallback is native now)."""


_patch_builder_for_fallback()
