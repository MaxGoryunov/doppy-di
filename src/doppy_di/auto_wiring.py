"""Type-based auto-wiring support.

Provides the ``@injectable`` decorator and helpers used by
``Container.scan()`` and lazy registration in ``Container.get()``.

Example:
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
from typing import Any, Optional, Tuple, Type, Union

from .container import Key, Rule


class MissingAnnotationError(TypeError):
    """Raised when an injectable class has an unannotated dependency.

    Example:
        >>> raise MissingAnnotationError(Service, "dep")
        Traceback (most recent call last):
        ...
        MissingAnnotationError: Missing annotation for 'dep' in
        <class '...Service'>
    """

    def __init__(self, cls: type, param: str) -> None:
        self.cls = cls
        self.param = param
        super().__init__(f"Missing annotation for {param!r} in {cls!r}")


class UnresolvableDependencyError(Exception):
    """Raised when an auto-wired dependency cannot be resolved.

    Example:
        >>> raise UnresolvableDependencyError(Service, Missing)
        Traceback (most recent call last):
        ...
        UnresolvableDependencyError: Unresolvable dependency:
        <class '...Service'> -> <class '...Missing'>
    """

    def __init__(self, key: Key, dep: Key) -> None:
        self.key = key
        self.dep = dep
        super().__init__(f"Unresolvable dependency: {key!r} -> {dep!r}")


_INJECTABLE_FLAG = "__doppy_injectable__"
_INJECTABLE_META = "__doppy_injectable_meta__"


def injectable(
    cls: Optional[Type[Any]] = None,
    *,
    scope: Optional[str] = None,
    qualifier: Optional[str] = None,
) -> Any:
    """Mark a class as a candidate for auto-registration.

    Usable bare (``@injectable``) or with options (``@injectable(scope=...)``).

    Args:
        cls: Class being decorated (bare usage).
        scope: Default lifetime for the class.
        qualifier: Named qualifier (reserved for future use).

    Example:
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


def _deps_of(cls: type) -> Tuple[type, ...]:
    """Extract annotated constructor dependencies for a class.

    Raises:
        MissingAnnotationError: If a parameter lacks an annotation.
    """
    if inspect.signature(cls) == inspect.signature(object):
        return ()
    sig = inspect.signature(cls)
    deps: list[type] = []
    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if param.annotation is inspect.Parameter.empty:
            raise MissingAnnotationError(cls, name)
        deps.append(param.annotation)
    return tuple(deps)


def _rule_for(cls: type) -> Rule:
    """Build a resolution rule for an injectable class."""
    meta = getattr(cls, _INJECTABLE_META, {}) or {}
    scope = meta.get("scope")
    return Rule(
        key=cls,
        make=cls,
        lifetime=scope or "singleton",
        deps=_deps_of(cls),
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
    pkg: Union[ModuleType, str],
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
