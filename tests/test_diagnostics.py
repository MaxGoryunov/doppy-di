"""Tests for rich diagnostic errors (issue 82)."""

import asyncio
from typing import Any, AsyncIterator, Iterator

import pytest

from doppy_di import (
    ContainerBuilder,
    DependencyCycleError,
    DuplicateKeyPolicy,
    DuplicateRegistrationError,
    FactoryExecutionError,
    InvalidLifetimeError,
    MissingDependencyError,
    RegistrationSource,
    ResourceFinalizationError,
    ScopeViolationError,
    ServiceNotFoundError,
)


def test_missing_dependency_has_path() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda c: c, deps=["c"])
    container = builder.build()

    with pytest.raises(MissingDependencyError) as exc:
        container.get("a")

    assert exc.value.key == "c"
    assert exc.value.resolution_path == ["a", "b", "c"]


def test_root_missing_still_raises_service_not_found() -> None:
    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(ServiceNotFoundError):
        container.get("missing")


def test_missing_dependency_is_service_not_found_subclass() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(ServiceNotFoundError):
        container.get("a")


def test_cycle_error_reports_cycle() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    with pytest.raises(DependencyCycleError) as exc:
        builder.service("b", lambda a: a, deps=["a"])

    assert "a" in exc.value.cycle
    assert "b" in exc.value.cycle


def test_cycle_error_is_cycle_error_subclass() -> None:
    from doppy_di import CycleError

    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    with pytest.raises(CycleError):
        builder.service("b", lambda a: a, deps=["a"])


def test_deferred_cycle_detected_at_resolve() -> None:
    builder = ContainerBuilder(check_cycles_on_register=False)
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()

    with pytest.raises(DependencyCycleError) as exc:
        container.get("a")

    assert "a" in exc.value.cycle
    assert "b" in exc.value.cycle


def test_invalid_lifetime_error() -> None:
    builder = ContainerBuilder()
    with pytest.raises(InvalidLifetimeError):
        builder.service("x", lambda: 1, lifetime="per_request")


def test_factory_error_wraps_original() -> None:
    def boom() -> None:
        raise ValueError("bad")

    builder = ContainerBuilder(wrap_factory_errors=True)
    builder.service("a", boom)
    container = builder.build()

    with pytest.raises(FactoryExecutionError) as exc:
        container.get("a")

    assert isinstance(exc.value.original_exception, ValueError)
    assert "a" in exc.value.resolution_path


def test_factory_error_default_not_wrapped() -> None:
    def boom() -> None:
        raise ValueError("bad")

    builder = ContainerBuilder()
    builder.service("a", boom)
    container = builder.build()

    with pytest.raises(ValueError, match="bad"):
        container.get("a")


def test_factory_error_wraps_async() -> None:
    async def boom() -> None:
        raise ValueError("bad")

    builder = ContainerBuilder(wrap_factory_errors=True)
    builder.service("a", boom)
    container = builder.build()

    with pytest.raises(FactoryExecutionError) as exc:
        asyncio.run(container.aget("a"))

    assert isinstance(exc.value.original_exception, ValueError)


def test_duplicate_registration_reports_sources() -> None:
    builder = ContainerBuilder(
        duplicate_policy=DuplicateKeyPolicy.FAIL,
        track_sources=True,
    )
    builder.value("x", 1)
    with pytest.raises(DuplicateRegistrationError) as exc:
        builder.value("x", 2)

    assert exc.value.key == "x"
    assert exc.value.existing_source is not None
    assert exc.value.new_source is not None


def test_duplicate_key_error_is_duplicate_registration() -> None:
    builder = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.FAIL)
    builder.value("x", 1)
    with pytest.raises(DuplicateRegistrationError):
        builder.value("x", 2)


def test_track_sources_records_location() -> None:
    builder = ContainerBuilder(track_sources=True)
    builder.value("x", 1)
    container = builder.build()

    source = container.config.ruleset.map["x"].registration_source
    assert source is not None
    assert isinstance(source, RegistrationSource)
    assert source.filename.endswith("test_diagnostics.py")


def test_track_sources_false_skips_inspect() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    container = builder.build()

    assert container.config.ruleset.map["x"].registration_source is None


def test_error_formatting_renders_tree() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(MissingDependencyError) as exc:
        container.get("a")

    text = str(exc.value)
    assert "Cannot resolve" in text
    assert "← missing" in text
    assert "Resolution path:" in text


def test_scope_name_in_missing_dependency() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(MissingDependencyError) as exc, container.scope("req") as scope:
        scope.get("a")

    assert exc.value.scope == "req"


def test_resource_finalization_error_flag() -> None:
    def make_bad() -> Iterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder(finalization_errors=True)
    builder.service("bad", make_bad)
    container = builder.build()

    with pytest.raises(ResourceFinalizationError) as exc, container.scope("req") as scope:
        scope.get("bad")

    assert len(exc.value.errors) == 1
    assert exc.value.errors[0][0] == "bad"


def test_resource_finalization_default_logs(caplog: Any) -> None:
    def make_bad() -> Iterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder()
    builder.service("bad", make_bad)
    container = builder.build()

    with container.scope("req") as scope:
        scope.get("bad")

    assert "Error finalizing yield provider" in caplog.text


def test_async_resource_finalization_error_flag() -> None:
    async def make_bad() -> AsyncIterator[object]:
        try:
            yield object()
        finally:
            raise RuntimeError("close failed")

    builder = ContainerBuilder(finalization_errors=True)
    builder.service("bad", make_bad)
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            await scope.get("bad")

    with pytest.raises(ResourceFinalizationError):
        asyncio.run(main())


def test_scope_violation_error_fields() -> None:
    err = ScopeViolationError("x", "req", "lifetime_mismatch")
    assert err.key == "x"
    assert err.scope == "req"
    assert err.violation_type == "lifetime_mismatch"


def test_aget_missing_dependency_has_path() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda c: c, deps=["c"])
    container = builder.build()

    with pytest.raises(MissingDependencyError) as exc:
        asyncio.run(container.aget("a"))

    assert exc.value.key == "c"
    assert "a" in exc.value.resolution_path
    assert "c" in exc.value.resolution_path


def test_aget_factory_error_wraps() -> None:
    async def boom() -> None:
        raise ValueError("bad")

    builder = ContainerBuilder(wrap_factory_errors=True)
    builder.service("a", boom)
    container = builder.build()

    with pytest.raises(FactoryExecutionError) as exc:
        asyncio.run(container.aget("a"))

    assert isinstance(exc.value.original_exception, ValueError)
    assert "a" in exc.value.resolution_path


def test_aget_deferred_cycle_detected() -> None:
    builder = ContainerBuilder(check_cycles_on_register=False)
    builder.service("a", lambda b: b, deps=["b"])
    builder.service("b", lambda a: a, deps=["a"])
    container = builder.build()

    with pytest.raises(DependencyCycleError):
        asyncio.run(container.aget("a"))


def test_aget_async_scope_missing_dependency_has_scope() -> None:
    builder = ContainerBuilder()
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    async def main() -> None:
        async with container.ascope("req") as scope:
            await scope.get("a")

    with pytest.raises(MissingDependencyError) as exc:
        asyncio.run(main())

    assert exc.value.scope == "req"


def test_aget_cancellation_finalization_error_flag() -> None:
    async def make_slow() -> int:
        await asyncio.sleep(10)
        return 1

    builder = ContainerBuilder(finalization_errors=True)
    builder.service("slow", make_slow)
    container = builder.build()

    async def main() -> None:
        from contextlib import AsyncExitStack
        from types import TracebackType
        from typing import Optional, Type

        async def bad_exit(
            exc_type: Optional[Type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType],
        ) -> None:
            raise RuntimeError("close failed")

        stack = AsyncExitStack()
        stack.push_async_exit(bad_exit)
        task = asyncio.create_task(container.aget("slow", _stacks=[stack]))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(ResourceFinalizationError):
            await task

    asyncio.run(main())


def test_duplicate_registration_str_includes_sources() -> None:
    builder = ContainerBuilder(
        duplicate_policy=DuplicateKeyPolicy.FAIL,
        track_sources=True,
    )
    builder.value("x", 1)
    with pytest.raises(DuplicateRegistrationError) as exc:
        builder.value("x", 2)

    text = str(exc.value)
    assert "Duplicate registration" in text
    assert "existing:" in text
    assert "new:" in text


def test_missing_dependency_str_includes_scope_and_source() -> None:
    builder = ContainerBuilder(track_sources=True)
    builder.service("a", lambda b: b, deps=["b"])
    container = builder.build()

    with pytest.raises(MissingDependencyError) as exc, container.scope("req") as scope:
        scope.get("a")

    text = str(exc.value)
    assert "Requested scope: req" in text
    assert "Registration source:" in text
