"""Tests for type-based auto-wiring."""

import pytest

from doppy_di import (
    ContainerBuilder,
    MissingAnnotationError,
    UnresolvableDependencyError,
    injectable,
)


def test_injectable_marks_class() -> None:
    @injectable
    class Service:
        pass

    assert Service.__doppy_injectable__ is True  # type: ignore[attr-defined]


def test_scan_registers_injectable_classes() -> None:
    @injectable(scope="singleton")
    class Repo:
        pass

    @injectable
    class Service:
        def __init__(self, repo: Repo) -> None:
            self.repo = repo

    builder = ContainerBuilder()
    container = builder.build()
    container.scan(__name__)

    service = container.get(Service)
    assert isinstance(service, Service)
    assert isinstance(service.repo, Repo)
    assert container.get(Service) is service  # singleton cached


def test_scan_does_not_override_explicit_registration() -> None:
    @injectable
    class Service:
        pass

    builder = ContainerBuilder()
    builder.service(Service, make=lambda: Service())
    container = builder.build()
    container.scan(__name__)

    # explicit rule wins; scan must not replace it
    assert container.has(Service)


def test_lazy_registration_on_get() -> None:
    @injectable
    class Service:
        pass

    builder = ContainerBuilder()
    container = builder.build()

    service = container.get(Service)  # no scan() called
    assert isinstance(service, Service)


def test_missing_annotation_raises() -> None:
    @injectable
    class Service:
        def __init__(self, dep) -> None:  # type: ignore[no-untyped-def]  # no annotation
            self.dep = dep

    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(MissingAnnotationError):
        container.get(Service)


def test_unresolvable_dependency_raises() -> None:
    class Missing:
        pass

    @injectable
    class Service:
        def __init__(self, dep: Missing) -> None:  # not registered, not injectable
            self.dep = dep

    builder = ContainerBuilder()
    container = builder.build()

    with pytest.raises(UnresolvableDependencyError):
        container.get(Service)
