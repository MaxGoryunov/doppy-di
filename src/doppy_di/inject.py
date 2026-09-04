"""Function and method injection support.

Provides the ``@inject`` decorator and ``Depends()`` helper for injecting
dependencies into plain functions and methods.

Examples:
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
from typing import Any, Callable, Optional, Protocol, Type, Union, cast, get_args, get_origin

from .auto_wiring import MissingAnnotationError, UnresolvableDependencyError
from .container import (
    _ACTIVE_REQUEST_RESOLVER,
    Container,
    Key,
    Qualifier,
)


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

    Examples:
        >>> Depends()
        <doppy_di.inject._DependsMarker object at ...>
    """
    return _DependsMarker(dependency)


class _ExternalMarker:
    """Marker wrapping an assisted-injection runtime argument for ``External()``."""

    __slots__ = ()


def External() -> Any:  # noqa: N802
    """Declare a runtime parameter supplied at build time (assisted injection).

    Parameters whose default is an ``External()`` marker are *not* resolved
    from the container; the caller passes them as keyword arguments when
    calling ``build(**kwargs)`` (or through ``@inject``'s reflected call).

    Examples:
        >>> External()
        <doppy_di.inject._ExternalMarker object at ...>
    """
    return _ExternalMarker()


class _PassthroughMarker:
    """Marker exempting a parameter from ``@inject`` resolution.

    A parameter whose default is a ``Pass()`` marker is left untouched by
    the injection wrapper. The framework (FastAPI ``Request``, gRPC ``call``,
    a declaratively-injected object) supplies it at call time instead.
    """

    __slots__ = ()


def Pass() -> Any:  # noqa: N802
    """Exempt a framework-supplied parameter from injection.

    Handlers wrapped by ``@inject`` normally resolve every annotated
    parameter from the container. Some frameworks fill parameters themselves
    (FastAPI ``Request``, gRPC ``ServerInterceptor`` call tuples). Mark those
    with ``= Pass()`` so ``@inject`` neither resolves them nor raises
    :class:`MissingAnnotationError`.

    Examples:
        >>> Pass()
        <doppy_di.inject._PassthroughMarker object at ...>
    """
    return _PassthroughMarker()


class MissingExternalArgumentError(TypeError):
    """Raised when a declared ``External()`` argument is not supplied."""

    def __init__(self, func: Callable[..., Any], name: str) -> None:
        self.func = func
        self.name = name
        super().__init__(f"Missing external argument {name!r} for {func!r}")


_Plan = tuple[
    dict[str, _DependsMarker],
    dict[str, Any],
    set[str],
    set[str],
    set[str],
]


def _annotation_key(annotation: Any) -> Any:
    """Convert an annotation into a container lookup key."""
    if get_origin(annotation) is not None and hasattr(annotation, "__metadata__"):
        for meta in getattr(annotation, "__metadata__", ()):
            if isinstance(meta, Qualifier):
                return cast(Key, (get_args(annotation)[0], meta.name))
    return annotation


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
    external: set[str] = set()
    unannotated: set[str] = set()
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if name in ("self", "cls"):
            continue
        if isinstance(param.default, _PassthroughMarker):
            continue
        if isinstance(param.default, _ExternalMarker):
            external.add(name)
            if param.annotation is not inspect.Parameter.empty and param.annotation is not Any:
                annotations[name] = param.annotation
        elif isinstance(param.default, _DependsMarker):
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
    return markers, annotations, injected, external, unannotated


def _resolve_all(
    resolver: _Resolver,
    func: Callable[..., Any],
    bound: inspect.BoundArguments,
    markers: dict[str, _DependsMarker],
    annotations: dict[str, Any],
    injected: set[str],
    external: set[str],
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
                    resolved[name] = resolver.get(_annotation_key(annotation))
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
                resolved[name] = resolver.get(_annotation_key(annotation))
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
    external: set[str],
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
                    resolved[name] = await resolver.get(_annotation_key(annotation))
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
                resolved[name] = await resolver.get(_annotation_key(annotation))
            except Exception as exc:
                raise UnresolvableDependencyError(type(func), annotation) from exc
    return resolved


def _validate_external(
    func: Callable[..., Any],
    bound: inspect.BoundArguments,
    external: set[str],
) -> None:
    """Raise when a declared ``External()`` argument missing in the call."""
    for name in external:
        if name not in bound.arguments:
            raise MissingExternalArgumentError(func, name)


class _ContainerAsyncAdapter:
    """Async resolver adapter exposing ``Container.aget`` as ``get``."""

    __slots__ = ("_container",)

    def __init__(self, container: Container) -> None:
        self._container = container

    async def get(self, key: Key) -> Any:
        return await self._container.aget(key)


class _BoundAssisted:
    """Provider-bound assisted factory with container deps pre-resolved."""

    __slots__ = ("_deps", "_external", "_func")

    def __init__(
        self,
        func: Callable[..., Any],
        deps: dict[str, Any],
        external: set[str],
    ) -> None:
        self._func = func
        self._deps = deps
        self._external = external

    def build(self, **kwargs: Any) -> Any:
        for name in self._external:
            if name not in kwargs:
                raise MissingExternalArgumentError(self._func, name)
        return self._func(**{**self._deps, **kwargs})

    async def abuild(self, **kwargs: Any) -> Any:
        for name in self._external:
            if name not in kwargs:
                raise MissingExternalArgumentError(self._func, name)
        result = self._func(**{**self._deps, **kwargs})
        if inspect.isawaitable(result):
            return await result
        return result


class _AssistedBuilder:
    """Standalone assisted-injection builder for a single factory."""

    __slots__ = ("_container", "_func", "_plan")

    def __init__(
        self,
        func: Callable[..., Any],
        container: Optional[Container],
        plan: _Plan,
    ) -> None:
        self._func = func
        self._container = container
        self._plan = plan

    def _resolver(self) -> Any:
        active = _ACTIVE_REQUEST_RESOLVER.get()
        if active is not None:
            return active
        if self._container is None:
            raise RuntimeError("Assisted builder has no container and is not inside a scope")
        return self._container

    def build(self, **kwargs: Any) -> Any:
        func = self._func
        markers, annotations, injected, external, unannotated = self._plan
        bound = inspect.signature(func).bind_partial(**kwargs)
        _validate_external(func, bound, external)
        resolved = _resolve_all(
            self._resolver(),
            func,
            bound,
            markers,
            annotations,
            injected,
            external,
            unannotated,
        )
        return func(**{**resolved, **kwargs})

    async def abuild(self, **kwargs: Any) -> Any:
        func = self._func
        markers, annotations, injected, external, unannotated = self._plan
        bound = inspect.signature(func).bind_partial(**kwargs)
        _validate_external(func, bound, external)
        resolver = self._resolver()
        if isinstance(resolver, Container):
            async_resolver: _AsyncResolver = _ContainerAsyncAdapter(resolver)
        else:
            async_resolver = resolver
        resolved = await _resolve_all_async(
            async_resolver,
            func,
            bound,
            markers,
            annotations,
            injected,
            external,
            unannotated,
        )
        result = func(**{**resolved, **kwargs})
        if inspect.isawaitable(result):
            return await result
        return result


def assisted(
    func: Callable[..., Any],
    container: Optional[Container] = None,
) -> _AssistedBuilder:
    """Build an assisted-injection factory.

    Injected (annotated) parameters resolve from ``container`` (or the active
    scope); parameters declared with ``External()`` are supplied at
    ``build(**kwargs)`` / ``abuild(**kwargs)`` time.

    Examples:
        >>> from doppy_di.container import ContainerBuilder
        >>> builder = ContainerBuilder()
        >>> builder.value("repo", object())
        >>> container = builder.build()
        >>> def make(repo, user_id: int = External()):
        ...     return user_id
        >>> assisted(make, container=container).build(user_id=7)
        7
    """
    return _AssistedBuilder(func, container, _build_plan(func))


def inject(
    container: Container,
    scope: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a function or method to inject dependencies.

    Args:
        container: Container used for resolution.
        scope: Optional scope name for temporary dependencies.

    Examples:
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
                markers, annotations, injected, external, unannotated = _get_plan()
                bound = inspect.signature(func).bind_partial(*args, **kwargs)
                _validate_external(func, bound, external)
                if scope is not None:
                    async with container.ascope(scope) as s:
                        resolved = await _resolve_all_async(
                            s,
                            func,
                            bound,
                            markers,
                            annotations,
                            injected,
                            external,
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
                    external,
                    unannotated,
                )
                return await func(*args, **{**kwargs, **resolved})

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            markers, annotations, injected, external, unannotated = _get_plan()
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            _validate_external(func, bound, external)
            if scope is not None:
                with container.scope(scope) as s:
                    resolved = _resolve_all(
                        s,
                        func,
                        bound,
                        markers,
                        annotations,
                        injected,
                        external,
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
                external,
                unannotated,
            )
            return func(*args, **{**kwargs, **resolved})

        return sync_wrapper

    return decorate
