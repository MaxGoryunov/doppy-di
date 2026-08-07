"""Tests for function and method injection."""

import asyncio
from typing import Any

import pytest

from doppy_di import (
    ContainerBuilder,
    Depends,
    MissingAnnotationError,
    UnresolvableDependencyError,
    inject,
)


class UserService:
    pass


class Missing:
    pass


@pytest.fixture
def container() -> Any:
    builder = ContainerBuilder()
    builder.value(UserService, UserService())
    return builder.build()


def test_inject_resolves_annotated_args(container: Any) -> None:
    @inject(container=container)
    def handle(service: UserService) -> UserService:
        return service

    assert isinstance(handle(), UserService)


def test_inject_with_depends(container: Any) -> None:
    @inject(container=container)
    def handle(service: UserService = Depends()) -> UserService:  # noqa: B008
        return service

    assert isinstance(handle(), UserService)


def test_inject_depends_callable(container: Any) -> None:
    def make_service() -> UserService:
        return UserService()

    @inject(container=container)
    def handle(
        service: UserService = Depends(make_service),  # noqa: B008
    ) -> UserService:
        return service

    assert isinstance(handle(), UserService)


def test_inject_depends_type(container: Any) -> None:
    @inject(container=container)
    def handle(
        service: UserService = Depends(UserService),  # noqa: B008
    ) -> UserService:
        return service

    assert isinstance(handle(), UserService)


def test_inject_async(container: Any) -> None:
    @inject(container=container)
    async def handle(service: UserService) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_async_depends(container: Any) -> None:
    @inject(container=container)
    async def handle(
        service: UserService = Depends(),  # noqa: B008
    ) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_async_depends_callable(container: Any) -> None:
    def make_service() -> UserService:
        return UserService()

    @inject(container=container)
    async def handle(
        service: UserService = Depends(make_service),  # noqa: B008
    ) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_async_depends_type(container: Any) -> None:
    @inject(container=container)
    async def handle(
        service: UserService = Depends(UserService),  # noqa: B008
    ) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_missing_annotation_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service) -> Any:  # type: ignore[no-untyped-def]
        return service

    with pytest.raises(MissingAnnotationError):
        handle()


def test_inject_unannotated_passed_explicitly(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    def handle(  # type: ignore[no-untyped-def]
        service,
    ) -> UserService:
        return service  # type: ignore[no-any-return]

    assert handle(explicit) is explicit


def test_inject_none_annotation_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service: None) -> None:
        return service

    with pytest.raises(MissingAnnotationError):
        handle()


def test_inject_skips_varargs_and_kwargs(container: Any) -> None:
    @inject(container=container)
    def handle(
        *args: object,
        service: UserService,
        **kwargs: object,
    ) -> tuple[tuple[object, ...], dict[str, object], UserService]:
        return args, kwargs, service

    args, kwargs, service = handle("a", key="b")
    assert args == ("a",)
    assert kwargs == {"key": "b"}
    assert isinstance(service, UserService)


def test_inject_mixed_deps(container: Any) -> None:
    def make_service() -> UserService:
        return UserService()

    @inject(container=container)
    def handle(
        a: UserService,
        b: UserService = Depends(),  # noqa: B008
        c: UserService = Depends(UserService),  # noqa: B008
        d: UserService = Depends(make_service),  # noqa: B008
    ) -> tuple[UserService, UserService, UserService, UserService]:
        return a, b, c, d

    a, b, c, d = handle()
    assert all(isinstance(x, UserService) for x in (a, b, c, d))


def test_inject_unresolvable_dependency_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service: Missing) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        handle()


def test_inject_depends_no_annotation_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service: Any = Depends()) -> Any:  # noqa: B008
        return service

    with pytest.raises(MissingAnnotationError):
        handle()


def test_inject_depends_unresolvable_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service: Missing = Depends()) -> Missing:  # noqa: B008
        return service

    with pytest.raises(UnresolvableDependencyError):
        handle()


def test_inject_depends_type_unresolvable_raises(container: Any) -> None:
    @inject(container=container)
    def handle(service: Missing = Depends(Missing)) -> Missing:  # noqa: B008
        return service

    with pytest.raises(UnresolvableDependencyError):
        handle()


def test_inject_async_missing_annotation_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(service) -> Any:  # type: ignore[no-untyped-def]
        return service

    with pytest.raises(MissingAnnotationError):
        asyncio.run(handle())


def test_inject_async_unannotated_passed_explicitly(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    async def handle(  # type: ignore[no-untyped-def]
        service,
    ) -> UserService:
        return service  # type: ignore[no-any-return]

    result = asyncio.run(handle(explicit))
    assert result is explicit


def test_inject_async_none_annotation_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(service: None) -> None:
        return service

    with pytest.raises(MissingAnnotationError):
        asyncio.run(handle())


def test_inject_async_skips_varargs_and_kwargs(container: Any) -> None:
    @inject(container=container)
    async def handle(
        *args: object,
        service: UserService,
        **kwargs: object,
    ) -> tuple[tuple[object, ...], dict[str, object], UserService]:
        return args, kwargs, service

    args, kwargs, service = asyncio.run(handle("a", key="b"))
    assert args == ("a",)
    assert kwargs == {"key": "b"}
    assert isinstance(service, UserService)


def test_inject_async_mixed_deps(container: Any) -> None:
    def make_service() -> UserService:
        return UserService()

    @inject(container=container)
    async def handle(
        a: UserService,
        b: UserService = Depends(),  # noqa: B008
        c: UserService = Depends(UserService),  # noqa: B008
        d: UserService = Depends(make_service),  # noqa: B008
    ) -> tuple[UserService, UserService, UserService, UserService]:
        return a, b, c, d

    result = asyncio.run(handle())
    assert all(isinstance(x, UserService) for x in result)


def test_inject_async_unresolvable_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(service: Missing) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        asyncio.run(handle())


def test_inject_async_depends_no_annotation_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(service: Any = Depends()) -> Any:  # noqa: B008
        return service

    with pytest.raises(MissingAnnotationError):
        asyncio.run(handle())


def test_inject_async_depends_unresolvable_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(service: Missing = Depends()) -> Missing:  # noqa: B008
        return service

    with pytest.raises(UnresolvableDependencyError):
        asyncio.run(handle())


def test_inject_async_depends_type_unresolvable_raises(container: Any) -> None:
    @inject(container=container)
    async def handle(
        service: Missing = Depends(Missing),  # noqa: B008
    ) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        asyncio.run(handle())


def test_inject_passes_explicit_args(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    def handle(service: UserService) -> UserService:
        return service

    assert handle(explicit) is explicit


def test_inject_passes_explicit_kwargs(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    def handle(service: UserService) -> UserService:
        return service

    assert handle(service=explicit) is explicit


def test_inject_async_passes_explicit_args(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    async def handle(service: UserService) -> UserService:
        return service

    result = asyncio.run(handle(explicit))
    assert result is explicit


def test_inject_async_passes_explicit_kwargs(container: Any) -> None:
    explicit = UserService()

    @inject(container=container)
    async def handle(service: UserService) -> UserService:
        return service

    result = asyncio.run(handle(service=explicit))
    assert result is explicit


def test_inject_skips_defaulted_params(container: Any) -> None:
    default = UserService()

    @inject(container=container)
    def handle(service: UserService = default) -> UserService:
        return service

    assert handle() is default


def test_inject_async_skips_defaulted_params(container: Any) -> None:
    default = UserService()

    @inject(container=container)
    async def handle(service: UserService = default) -> UserService:
        return service

    result = asyncio.run(handle())
    assert result is default


def test_inject_method_skips_self(container: Any) -> None:
    class Handler:
        @inject(container=container)
        def handle(self, service: UserService) -> UserService:
            return service

    handler = Handler()
    assert isinstance(handler.handle(), UserService)


def test_inject_scope_sync(container: Any) -> None:
    @inject(container=container, scope="req")
    def handle(service: UserService) -> UserService:
        return service

    assert isinstance(handle(), UserService)


def test_inject_scope_async(container: Any) -> None:
    @inject(container=container, scope="req")
    async def handle(service: UserService) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_scope_sync_depends(container: Any) -> None:
    @inject(container=container, scope="req")
    def handle(service: UserService = Depends()) -> UserService:  # noqa: B008
        return service

    assert isinstance(handle(), UserService)


def test_inject_scope_sync_unresolvable_raises(container: Any) -> None:
    @inject(container=container, scope="req")
    def handle(service: Missing) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        handle()


def test_inject_scope_async_unresolvable_raises(container: Any) -> None:
    @inject(container=container, scope="req")
    async def handle(service: Missing) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        asyncio.run(handle())


def test_inject_scope_async_unannotated_raises(container: Any) -> None:
    @inject(container=container, scope="req")
    async def handle(service) -> Any:  # type: ignore[no-untyped-def]
        return service

    with pytest.raises(MissingAnnotationError):
        asyncio.run(handle())


def test_inject_scope_async_passes_explicit(container: Any) -> None:
    explicit = UserService()

    @inject(container=container, scope="req")
    async def handle(service: UserService) -> UserService:
        return service

    result = asyncio.run(handle(explicit))
    assert result is explicit


def test_inject_scope_async_depends_no_annotation_raises(
    container: Any,
) -> None:
    @inject(container=container, scope="req")
    async def handle(service: Any = Depends()) -> Any:  # noqa: B008
        return service

    with pytest.raises(MissingAnnotationError):
        asyncio.run(handle())


def test_inject_scope_async_depends_type_unresolvable_raises(
    container: Any,
) -> None:
    @inject(container=container, scope="req")
    async def handle(
        service: Missing = Depends(Missing),  # noqa: B008
    ) -> Missing:
        return service

    with pytest.raises(UnresolvableDependencyError):
        asyncio.run(handle())


def test_inject_scope_async_depends_callable(container: Any) -> None:
    def make_service() -> UserService:
        return UserService()

    @inject(container=container, scope="req")
    async def handle(
        service: UserService = Depends(make_service),  # noqa: B008
    ) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_scope_async_depends(container: Any) -> None:
    @inject(container=container, scope="req")
    async def handle(
        service: UserService = Depends(),  # noqa: B008
    ) -> UserService:
        return service

    result = asyncio.run(handle())
    assert isinstance(result, UserService)


def test_inject_signature_cached(container: Any) -> None:
    @inject(container=container)
    def handle(service: UserService) -> UserService:
        return service

    handle()
    handle()
    assert isinstance(handle(), UserService)


def test_inject_async_signature_cached(container: Any) -> None:
    @inject(container=container)
    async def handle(service: UserService) -> UserService:
        return service

    asyncio.run(handle())
    asyncio.run(handle())
    assert isinstance(asyncio.run(handle()), UserService)


def test_inject_wraps_preserves_metadata(container: Any) -> None:
    @inject(container=container)
    def handle(service: UserService) -> UserService:
        """Docstring."""
        return service

    assert handle.__name__ == "handle"
    assert handle.__doc__ == "Docstring."


def test_inject_async_wraps_preserves_metadata(container: Any) -> None:
    @inject(container=container)
    async def handle(service: UserService) -> UserService:
        """Docstring."""
        return service

    assert handle.__name__ == "handle"
    assert handle.__doc__ == "Docstring."


def test_depends_returns_marker() -> None:
    marker = Depends()
    assert marker.dependency is None
    assert Depends(UserService).dependency is UserService
