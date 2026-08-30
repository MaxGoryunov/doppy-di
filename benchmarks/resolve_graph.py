"""Benchmark doppy-di against Injex and other DI containers.

Adapted from upstream Injex benchmark (commit ba01497d00b233dc97e134791c9246e8139df65d).
Preserves upstream graph, timing methodology, and adapters. Adds doppy-di adapter.
"""

from __future__ import annotations

import gc
import importlib.metadata as metadata
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import punq
import wireup
from dependency_injector import containers, providers
from dishka import Provider, Scope, from_context, make_container, provide
from injex import Container as InjexContainer
from lagom import Container as LagomContainer
from wireup import injectable

from doppy_di import Container, ContainerBuilder


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///:memory:"
    smtp_url: str = "smtp://localhost"


class ApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings


class UserRepository:
    def __init__(self, client: ApiClient):
        self.client = client


class EmailSender:
    def __init__(self, client: ApiClient):
        self.client = client


class AuditLog:
    def __init__(self, settings: Settings):
        self.settings = settings


class RegisterUser:
    def __init__(
        self,
        repo: UserRepository,
        email: EmailSender,
        audit: AuditLog,
    ):
        self.repo = repo
        self.email = email
        self.audit = audit


settings = Settings()
manual_client = ApiClient(settings)


def manual_resolve() -> RegisterUser:
    return RegisterUser(
        UserRepository(manual_client),
        EmailSender(manual_client),
        AuditLog(settings),
    )


def setup_injex() -> Callable[[], RegisterUser]:
    container = InjexContainer()
    container.add_instance(Settings, settings)
    container.add_singleton(ApiClient)
    container.add_transient(UserRepository)
    container.add_transient(EmailSender)
    container.add_transient(AuditLog)
    container.add_transient(RegisterUser)
    container.assert_valid()
    container.resolve(RegisterUser)
    return lambda: container.resolve(RegisterUser)


def setup_punq() -> Callable[[], RegisterUser]:
    container = punq.Container()
    container.register(Settings, instance=settings)
    container.register(ApiClient, ApiClient)
    container.register(UserRepository, UserRepository)
    container.register(EmailSender, EmailSender)
    container.register(AuditLog, AuditLog)
    container.register(RegisterUser, RegisterUser)
    container.resolve(RegisterUser)
    return lambda: container.resolve(RegisterUser)


def setup_lagom() -> Callable[[], RegisterUser]:
    container = LagomContainer()
    container[Settings] = settings
    container[ApiClient] = ApiClient
    container[UserRepository] = UserRepository
    container[EmailSender] = EmailSender
    container[AuditLog] = AuditLog
    container[RegisterUser] = RegisterUser
    container[RegisterUser]
    return lambda: container[RegisterUser]


class DishkaProvider(Provider):  # type: ignore[misc, valid-type]
    # Same graph: Settings is a provided instance, ApiClient a singleton
    # (cache=True), the rest transient (cache=False) so each get() builds anew.
    settings = from_context(provides=Settings, scope=Scope.APP)  # type: ignore[assignment]
    api_client = provide(ApiClient, scope=Scope.APP)  # type: ignore[assignment]
    repo = provide(UserRepository, scope=Scope.APP, cache=False)  # type: ignore[assignment]
    email = provide(EmailSender, scope=Scope.APP, cache=False)  # type: ignore[assignment]
    audit = provide(AuditLog, scope=Scope.APP, cache=False)  # type: ignore[assignment]
    register_user = provide(RegisterUser, scope=Scope.APP, cache=False)


def setup_dishka() -> Callable[[], RegisterUser]:
    container = make_container(DishkaProvider(), context={Settings: settings})
    container.get(RegisterUser)
    return lambda: container.get(RegisterUser)


class DIContainer(containers.DeclarativeContainer):  # type: ignore[misc]
    settings_provider = providers.Object(settings)
    api_client = providers.Singleton(ApiClient, settings=settings_provider)
    repo = providers.Factory(UserRepository, client=api_client)
    email = providers.Factory(EmailSender, client=api_client)
    audit = providers.Factory(AuditLog, settings=settings_provider)
    register_user = providers.Factory(
        RegisterUser,
        repo=repo,
        email=email,
        audit=audit,
    )


def setup_dependency_injector() -> Callable[[], RegisterUser]:
    container = DIContainer()
    container.register_user()
    return lambda: container.register_user()


@injectable
class WSettings(Settings):
    pass


@injectable
class WApiClient:
    def __init__(self, settings: WSettings):
        self.settings = settings


@injectable(lifetime="transient")
class WUserRepository:
    def __init__(self, client: WApiClient):
        self.client = client


@injectable(lifetime="transient")
class WEmailSender:
    def __init__(self, client: WApiClient):
        self.client = client


@injectable(lifetime="transient")
class WAuditLog:
    def __init__(self, settings: WSettings):
        self.settings = settings


@injectable(lifetime="transient")
class WRegisterUser:
    def __init__(self, repo: WUserRepository, email: WEmailSender, audit: WAuditLog):
        self.repo = repo
        self.email = email
        self.audit = audit


def setup_wireup_scope_per_op() -> Callable[[], WRegisterUser]:
    container = wireup.create_sync_container(
        injectables=[
            WSettings,
            WApiClient,
            WUserRepository,
            WEmailSender,
            WAuditLog,
            WRegisterUser,
        ]
    )

    def resolve() -> WRegisterUser:
        with container.enter_scope() as scoped:
            return scoped.get(WRegisterUser)  # type: ignore[no-any-return]

    first = resolve()
    second = resolve()
    assert first is not second
    assert first.repo.client is second.repo.client
    return resolve


def setup_wireup_same_scope() -> Callable[[], WRegisterUser]:
    container = wireup.create_sync_container(
        injectables=[
            WSettings,
            WApiClient,
            WUserRepository,
            WEmailSender,
            WAuditLog,
            WRegisterUser,
        ]
    )
    scoped = container.enter_scope()
    scoped.__enter__()
    first = scoped.get(WRegisterUser)  # type: ignore[no-any-return]
    second = scoped.get(WRegisterUser)  # type: ignore[no-any-return]
    assert first is not second
    return lambda: scoped.get(WRegisterUser)  # type: ignore[no-any-return]


def _build_doppy_di() -> Container:
    builder = ContainerBuilder()
    builder.value(Settings, settings)
    builder.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    builder.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    builder.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    builder.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    builder.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )
    return builder.build()


def setup_doppy_di() -> Callable[[], RegisterUser]:
    """doppy-di adapter: normal production-like mode."""
    container = _build_doppy_di()
    container.get(RegisterUser)
    return lambda: container.get(RegisterUser)


def setup_doppy_di_compiled() -> Callable[[], RegisterUser]:
    """doppy-di adapter: compiled ExecutionPlan, allow-override path."""
    container = _build_doppy_di()
    plan = container.compile(allow_post_compile_overrides=True)
    plan.get(RegisterUser)
    return lambda: plan.get(RegisterUser)


def setup_doppy_di_frozen() -> Callable[[], RegisterUser]:
    """doppy-di adapter: compiled ExecutionPlan, frozen lockless path."""
    container = _build_doppy_di()
    plan = container.compile(allow_post_compile_overrides=False)
    plan.get(RegisterUser)
    return lambda: plan.get(RegisterUser)


def setup_doppy_di_guardless() -> Callable[[], RegisterUser]:
    """doppy-di adapter: guardless fast path (issue #120)."""
    container = _build_doppy_di()
    plan = container.compile(guardless=True)
    plan.get(RegisterUser)
    return lambda: plan.get(RegisterUser)


def bench(
    cases: list[tuple[str, Callable[[], object]]],
    *,
    iterations: int = 250_000,
    rounds: int = 11,
) -> dict[str, tuple[float, float, float]]:
    """Interleaved A/B benchmark.

    All cases are warmed up first, then measured round-by-round in
    interleaved order (A/B/A/B...) so GC and system noise hit every case
    equally. GC is disabled for the whole measurement window. Returns
    ``{name: (median, min, max)}`` in ns/op.
    """
    for _, fn in cases:
        for _ in range(12_000):
            fn()

    samples: dict[str, list[float]] = {name: [] for name, _ in cases}
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(rounds):
            for name, fn in cases:
                start = time.perf_counter_ns()
                for _ in range(iterations):
                    obj = fn()
                end = time.perf_counter_ns()
                assert obj is not None
                samples[name].append((end - start) / iterations)
    finally:
        if gc_was_enabled:
            gc.enable()

    return {name: (statistics.median(vals), min(vals), max(vals)) for name, vals in samples.items()}


def package_version(name: str) -> str:
    if name == "injex":
        try:
            return metadata.version("injex")
        except metadata.PackageNotFoundError:
            pass
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text())
            version = data.get("project", {}).get("version")
            if not version:
                # Hatch VCS or dynamic version might be used
                version = (
                    data.get("tool", {}).get("hatch", {}).get("version", {}).get("path", "unknown")
                )
            return f"{version} (local checkout)"
        except Exception:
            return "unknown (local checkout)"
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def semantic_checks(cases: list[tuple[str, Callable[[], object]]]) -> dict[str, str]:
    """Run smoke + identity checks per issue §Correctness checks."""
    results: dict[str, str] = {}
    for name, fn in cases:
        try:
            obj = fn()
            assert isinstance(obj, RegisterUser)
            assert isinstance(obj.repo, UserRepository)
            assert isinstance(obj.audit, AuditLog)
            assert isinstance(obj.repo.client, ApiClient)
            assert isinstance(obj.repo.client.settings, Settings)

            a = fn()
            b = fn()
            assert a is not b
            assert a.repo is not b.repo  # type: ignore[attr-defined]
            assert a.audit is not b.audit  # type: ignore[attr-defined]
            assert a.repo.client is b.repo.client  # type: ignore[attr-defined]
            assert a.repo.client.settings is b.repo.client.settings  # type: ignore[attr-defined]
            results[name] = "PASS"
        except Exception as exc:
            results[name] = f"FAIL ({exc!r})"
    return results


def cold_start_bench(
    name: str,
    setup: Callable[[], Callable[[], object]],
    *,
    samples: int = 9,
) -> float:
    """Build+register+validate+first resolve, fresh container per sample."""
    times = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        fn = setup()
        fn()
        times.append((time.perf_counter_ns() - start) / 1_000_000)  # ms
    return statistics.median(times)


def main() -> None:
    env_lines = []
    env_lines.append(f"Python: {platform.python_version()} ({platform.machine()})")
    env_lines.append(f"Platform: {platform.platform()}")
    env_lines.append(f"Processor: {platform.processor()}")
    env_lines.append(f"CPU count: {os.cpu_count()}")
    env_lines.append(f"Benchmark commit: {os.environ.get('GIT_COMMIT', 'unknown')}")
    for package in [
        "injex",
        "wireup",
        "dishka",
        "dependency-injector",
        "lagom",
        "punq",
        "doppy-di",
    ]:
        env_lines.append(f"{package}: {package_version(package)}")
    env_lines.append("Warmup iterations: 12_000")
    env_lines.append("Measured iterations: 250_000")
    env_lines.append("Samples: 9")
    print("\n".join(env_lines))

    cases = [
        ("manual", manual_resolve),
        ("injex", setup_injex()),
        ("doppy-di", setup_doppy_di()),
        ("doppy-di compiled", setup_doppy_di_compiled()),
        ("doppy-di frozen", setup_doppy_di_frozen()),
        ("doppy-di guardless", setup_doppy_di_guardless()),
        ("wireup same scope", setup_wireup_same_scope()),
        ("wireup scope/op", setup_wireup_scope_per_op()),
        ("dishka", setup_dishka()),
        ("dependency-injector", setup_dependency_injector()),
        ("lagom", setup_lagom()),
        ("punq", setup_punq()),
    ]

    print("\nGraph")
    print("Settings: singleton")
    print("ApiClient(Settings): singleton")
    print("UserRepository(ApiClient): transient")
    print("EmailSender(ApiClient): transient")
    print("AuditLog(Settings): transient")
    print("RegisterUser(UserRepository, EmailSender, AuditLog): transient")

    print("\nResolve benchmark")
    print("singleton Settings/ApiClient + transient Repo/Email/Audit/RegisterUser")
    results = bench(cases)
    baseline = results["manual"][0]

    print(f"{'library':<22} {'median µs/op':>14} {'x manual':>10} {'min..max µs':>18}")
    for name, (median, min_value, max_value) in sorted(results.items(), key=lambda row: row[1][0]):
        print(
            f"{name:<22} {median / 1000:>14.3f} {median / baseline:>10.2f} "
            f"{min_value / 1000:>7.3f}..{max_value / 1000:<7.3f}"
        )

    frozen = results["doppy-di frozen"][0]
    allow = results["doppy-di compiled"][0]
    print(f"\nfrozen/allow ratio (median): {frozen / allow:.3f}")

    print("\nSemantic checks")
    semantic = semantic_checks(cases)
    for name, status in semantic.items():
        print(f"{name}: {status}")

    # Cold-start benchmark (optional, separate from hot-resolve)
    print("\nCold-start results (build+register+validate+first resolve)")
    cold_cases = [
        ("injex", setup_injex),
        ("doppy-di", setup_doppy_di),
        ("doppy-di compiled", setup_doppy_di_compiled),
        ("doppy-di guardless", setup_doppy_di_guardless),
        ("wireup same scope", setup_wireup_same_scope),
        ("wireup scope/op", setup_wireup_scope_per_op),
        ("dishka", setup_dishka),
        ("dependency-injector", setup_dependency_injector),
        ("lagom", setup_lagom),
        ("punq", setup_punq),
    ]
    cold_results = {name: cold_start_bench(name, setup) for name, setup in cold_cases}
    print(f"{'library':<22} {'median ms':>14}")
    for name, ms in sorted(cold_results.items(), key=lambda row: row[1]):
        print(f"{name:<22} {ms:>14.3f}")

    # Save artifacts
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)

    raw_path = results_dir / f"{timestamp}-raw.txt"
    raw_lines = [
        "Environment",
        "-----------",
        *env_lines,
        "",
        "Graph",
        "-----",
        "Settings: singleton",
        "ApiClient(Settings): singleton",
        "UserRepository(ApiClient): transient",
        "EmailSender(ApiClient): transient",
        "AuditLog(Settings): transient",
        "RegisterUser(UserRepository, EmailSender, AuditLog): transient",
        "",
        "Results",
        "-------",
        f"{'Implementation':<22} {'Median us/op':>14} {'Min us/op':>10} "
        f"{'Max us/op':>10} {'Overhead vs manual':>20}",
    ]
    for name, (median, min_value, max_value) in sorted(results.items(), key=lambda row: row[1][0]):
        overhead = ((median / baseline) - 1.0) * 100.0
        raw_lines.append(
            f"{name:<22} {median / 1000:>14.3f} {min_value / 1000:>10.3f} "
            f"{max_value / 1000:>10.3f} {overhead:>+19.1f}%"
        )
    raw_lines.append("")
    raw_lines.append("Semantic checks")
    raw_lines.append("---------------")
    for name, status in semantic.items():
        raw_lines.append(f"{name}: {status}")
    raw_lines.append("")
    raw_lines.append("Cold-start results")
    raw_lines.append("------------------")
    raw_lines.append(f"{'Implementation':<22} {'Build+register+validate+first resolve':>14}")
    for name, ms in sorted(cold_results.items(), key=lambda row: row[1]):
        raw_lines.append(f"{name:<22} {ms:>14.3f} ms")
    raw_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    env_path = results_dir / f"{timestamp}-environment.txt"
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    summary = {
        "benchmark_commit": os.environ.get("GIT_COMMIT", "unknown"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "graph": {
            "Settings": "singleton",
            "ApiClient": "singleton",
            "UserRepository": "transient",
            "EmailSender": "transient",
            "AuditLog": "transient",
            "RegisterUser": "transient",
        },
        "warmup": 12_000,
        "iterations": 250_000,
        "samples": 9,
        "results": {
            name: {
                "median_us_per_op": median / 1000,
                "min_us_per_op": min_value / 1000,
                "max_us_per_op": max_value / 1000,
            }
            for name, (median, min_value, max_value) in results.items()
        },
        "semantic_checks": semantic,
        "cold_start_ms": cold_results,
    }
    summary_path = results_dir / f"{timestamp}-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
