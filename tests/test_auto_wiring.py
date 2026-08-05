"""Tests for type-based auto-wiring."""

import importlib
import sys
from pathlib import Path
from typing import cast

import pytest

from doppy_di import (
    ContainerBuilder,
    MissingAnnotationError,
    UnresolvableDependencyError,
    injectable,
)


@injectable
class ModuleService:
    pass


@injectable(scope="singleton")
class ModuleRepo:
    pass


_DEFAULT_REPO = ModuleRepo()


@injectable
class ModuleWithDefault:
    def __init__(self, dep: ModuleRepo = _DEFAULT_REPO) -> None:
        self.dep = dep


def make_injectable_class(name: str) -> type:
    return cast(type, injectable(type(name, (), {})))


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
    explicit = object()

    builder = ContainerBuilder()
    builder.value(ModuleService, explicit)
    container = builder.build()
    container.scan(__name__)

    # explicit rule wins; scan must not replace it
    assert container.get(ModuleService) is explicit


def test_scan_finds_module_level_classes() -> None:
    builder = ContainerBuilder()
    container = builder.build()
    container.scan(__name__)

    assert container.has(ModuleService)
    assert container.has(ModuleRepo)
    assert container.get(ModuleService) is container.get(ModuleService)


def test_defaulted_param_skipped() -> None:
    builder = ContainerBuilder()
    container = builder.build()
    container.scan(__name__)

    service = container.get(ModuleWithDefault)
    assert isinstance(service, ModuleWithDefault)


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


def test_scan_recursive_package(tmp_path: Path) -> None:
    name = "_doppy_scan_test_pkg"
    pkg_dir = tmp_path / name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").touch()
    (pkg_dir / "sub.py").write_text(
        "from test_auto_wiring import make_injectable_class\n"
        "_SubService = make_injectable_class('SubService')\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module(name)
        sub = importlib.import_module(f"{name}.sub")

        builder = ContainerBuilder()
        container = builder.build()
        container.scan(name)

        assert container.has(sub._SubService)
    finally:
        for mod in list(sys.modules):
            if mod == name or mod.startswith(name + "."):
                del sys.modules[mod]
        sys.path.remove(str(tmp_path))
