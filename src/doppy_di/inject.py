"""Function and method injection support.

Provides the ``@inject`` decorator and ``Depends()`` helper for injecting
dependencies into plain functions and methods.

Example:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("service", 42)
    >>> container = builder.build()
    >>> @inject(container=container)
    ... def answer(service: int):
    ...     return service
    >>> answer()
    42
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Optional, Protocol, Type, Union

from .auto_wiring import MissingAnnotationError, UnresolvableDependencyError
from .container import Container, Key


class _Resolver(Protocol):
    """Minimal sync resolver interface shared by Container and Scope."""

    def get(self, key: Key) -> Any: ...


class _AsyncResolver(Protocol):
    """Minimal async resolver interface shared by AsyncScope."""

    async def get(self, key: Key) -> Any: ...


class _DependsMarker:
    """Marker wrapping a dependency declaration for ``Depends()``."""

    __slots__ = ("dependency",)

    def __init__(
        self,
        dependency: Optional[Union[Type[Any], Callable[..., Any]]],
    ) -> None:
        self.dependency = dependency


def Depends(  # noqa: N802
    dependency: Optional[Union[Type[Any], Callable[..., Any]]] = None,
) -> Any:
    """Declare a dependency for injection.

    Args:
        dependency: Type to resolve from container, callable to invoke, or
            ``None`` to fall back to the argument annotation.

    Example:
        >>> Depends()
        <doppy_di.inject._DependsMarker object at ...>
    """
    return _DependsMarker(dependency)


_Plan = tuple[
    dict[str, _DependsMarker],
    dict[str, Any],
    set[str],
    set[str],
]


def _build_plan(func: Callable[..., Any]) -> _Plan:
    """Extract injection plan from function signature.

    Returns:
        (markers, annotations, injected_names, unannotated_names) where
        markers maps parameter names to their ``Depends()`` markers,
        annotations maps parameter names to their type annotations,
        injected_names is the set of parameter names that require injection,
        and unannotated_names is the set of parameter names that lack both
        annotation and ``Depends()``.
    """
    sig = inspect.signature(func)
    markers: dict[str, _DependsMarker] = {}
    annotations: dict[str, Any] = {}
    injected: set[str] = set()
    unannotated: set[str] = set()
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if name in ("self", "cls"):
            continue
        if isinstance(param.default, _DependsMarker):
            markers[name] = param.default
            injected.add(name)
            if param.annotation is not inspect.Parameter.empty and param.annotation is not Any:
                annotations[name] = param.annotation
        elif param.default is not inspect.Parameter.empty:
            continue
        elif param.annotation is not inspect.Parameter.empty:
            annotations[name] = param.annotation
            injected.add(name)
        else:
            unannotated.add(name)
    return markers, annotations, injected, unannotated


def _resolve_all(
    resolver: _Resolver,
    func: Callable[..., Any],
    bound: inspect.BoundArguments,
    markers: dict[str, _DependsMarker],
    annotations: dict[str, Any],
    injected: set[str],
    unannotated: set[str],
) -> dict[str, Any]:
    """Resolve all missing injected arguments for a sync call."""
    resolved: dict[str, Any] = {}
    for name in unannotated:
        if name not in bound.arguments:
            raise MissingAnnotationError(type(func), name)
    for name in injected:
        if name in bound.arguments:
            continue
        marker = markers.get(name)
        if marker is not None:
            dep = marker.dependency
            if dep is None:
                annotation = annotations.get(name)
                if annotation is None:
                    raise MissingAnnotationError(type(func), name)
                try:
                    resolved[name] = resolver.get(annotation)
                except Exception as exc:
                    raise UnresolvableDependencyError(type(func), annotation) from exc
            elif isinstance(dep, type):
                try:
                    resolved[name] = resolver.get(dep)
                except Exception as exc:
                    raise UnresolvableDependencyError(type(func), dep) from exc
            else:
                resolved[name] = dep()
        else:
            annotation = annotations.get(name)
            if annotation is None:
                raise MissingAnnotationError(type(func), name)
            try:
                resolved[name] = resolver.get(annotation)
            except Exception as exc:
                raise UnresolvableDependencyError(type(func), annotation) from exc
    return resolved


async def _resolve_all_async(
    resolver: _AsyncResolver,
    func: Callable[..., Any],
    bound: inspect.BoundArguments,
    markers: dict[str, _DependsMarker],
    annotations: dict[str, Any],
    injected: set[str],
    unannotated: set[str],
) -> dict[str, Any]:
    """Resolve all missing injected arguments for an async call."""
    resolved: dict[str, Any] = {}
    for name in unannotated:
        if name not in bound.arguments:
            raise MissingAnnotationError(type(func), name)
    for name in injected:
        if name in bound.arguments:
            continue
        marker = markers.get(name)
        if marker is not None:
            dep = marker.dependency
            if dep is None:
                annotation = annotations.get(name)
                if annotation is None:
                    raise MissingAnnotationError(type(func), name)
                try:
                    resolved[name] = await resolver.get(annotation)
                except Exception as exc:
                    raise UnresolvableDependencyError(type(func), annotation) from exc
            elif isinstance(dep, type):
                try:
                    resolved[name] = await resolver.get(dep)
                except Exception as exc:
                    raise UnresolvableDependencyError(type(func), dep) from exc
            else:
                resolved[name] = dep()
        else:
            annotation = annotations.get(name)
            if annotation is None:
                raise MissingAnnotationError(type(func), name)
            try:
                resolved[name] = await resolver.get(annotation)
            except Exception as exc:
                raise UnresolvableDependencyError(type(func), annotation) from exc
    return resolved


def inject(
    container: Container,
    scope: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a function or method to inject dependencies.

    Args:
        container: Container used for resolution.
        scope: Optional scope name for temporary dependencies.

    Example:
        >>> builder = ContainerBuilder()
        >>> builder.value("service", 42)
        >>> container = builder.build()
        >>> @inject(container=container)
        ... def answer(service: int):
        ...     return service
        >>> answer()
        42
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        plan: Optional[_Plan] = None

        def _get_plan() -> _Plan:
            nonlocal plan
            if plan is None:
                plan = _build_plan(func)
            return plan

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                markers, annotations, injected, unannotated = _get_plan()
                bound = inspect.signature(func).bind_partial(*args, **kwargs)
                if scope is not None:
                    async with container.ascope(scope) as s:
                        resolved = await _resolve_all_async(
                            s,
                            func,
                            bound,
                            markers,
                            annotations,
                            injected,
                            unannotated,
                        )
                        return await func(*args, **{**kwargs, **resolved})
                resolved = _resolve_all(
                    container,
                    func,
                    bound,
                    markers,
                    annotations,
                    injected,
                    unannotated,
                )
                return await func(*args, **{**kwargs, **resolved})

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            markers, annotations, injected, unannotated = _get_plan()
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            if scope is not None:
                with container.scope(scope) as s:
                    resolved = _resolve_all(
                        s,
                        func,
                        bound,
                        markers,
                        annotations,
                        injected,
                        unannotated,
                    )
                    return func(*args, **{**kwargs, **resolved})
            resolved = _resolve_all(
                container,
                func,
                bound,
                markers,
                annotations,
                injected,
                unannotated,
            )
            return func(*args, **{**kwargs, **resolved})

        return sync_wrapper

    return decorate
