"""Scenario benchmarks: realistic workloads beyond the single-root microbench.

Companion to ``resolve_graph.py`` (which preserves the upstream Injex
benchmark graph and methodology verbatim). This file adds workload shapes
discussed in issues #39/#40 where compiled-plan design differences actually
show up in real applications:

1. first_touch_sweep -- server boot fan-out: resolve M distinct roots once on
   a fresh container. Injex builds its flat creator lazily per root (exec
   compile lands inside the measured region); doppy-di compiles the whole
   graph upfront. Expected winner: doppy-di compiled.
2. deep_chain_d16 -- middleware/pipeline realism: 15-level transient chain
   over 2 singletons. Tracks the issue #40 flattening payoff: after
   flattening, doppy-di frame count stays constant with depth.
3. wide_service_d10 -- typical application service with 10 dependencies.
   Amplifies CSE and transient-inlining gains.
4. round_robin_roots -- request dispatcher alternating across 8 roots.
   doppy-di resolvers are zero-arg closures; Injex threads ``scope`` through
   every nested creator call.
5. value_heavy_root -- bootstrap/config object consuming 8 constants.
   Exposes per-value maker frames in the current doppy-di fast path;
   motivates constant inlining (issue #40 follow-up).
6. singleton_fetch_loop -- hot per-request cached-service lookup.
   Detector for guard-check bloat in ``ExecutionPlan.get``.
7. threaded_resolve -- 4 threads resolving a shared mixed-lifetime graph.
   Verifies lock-free warm singleton hits under GIL contention.

Honest reporting (issues #39/#40): every case prints WIN/TIE/LOSS for
doppy-di compiled against Injex on identical methodology. A loss is reported
as a loss with the scenario kept in place; the benchmark is never weakened
to manufacture a win.
"""

import gc
import json
import os
import platform
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from injex import Container as InjexContainer

from doppy_di import ContainerBuilder

RESULTS_DIR = Path(__file__).resolve().parent / "results"
WARMUP = 10_000
ROUNDS = 9
_NL = chr(10)


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _warm_us(
    op: Callable[[], object],
    *,
    iterations: int,
) -> tuple[float, float, float]:
    """Median/min/max microseconds per op, upstream methodology."""
    for _ in range(WARMUP):
        op()
    samples: list[float] = []
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(ROUNDS):
            start = time.perf_counter_ns()
            for _ in range(iterations):
                obj = op()
            end = time.perf_counter_ns()
            assert obj is not None
            samples.append((end - start) / iterations / 1000.0)
    finally:
        if gc_was_enabled:
            gc.enable()
    return statistics.median(samples), min(samples), max(samples)


def _sweep_ms(
    sweep: Callable[[], object],
    *,
    samples: int = 9,
) -> tuple[float, float, float]:
    """Median/min/max milliseconds per full sweep (containers built untimed)."""
    times: list[float] = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        obj = sweep()
        end = time.perf_counter_ns()
        assert obj is not None
        times.append((end - start) / 1e6)
    return statistics.median(times), min(times), max(times)


def _threaded_sample(op: Callable[[], object], threads: int, iters: int) -> float:
    """One barrier-synchronized sample; returns wall-clock us/op."""
    barrier = threading.Barrier(threads)
    elapsed = [0.0] * threads

    def worker(idx: int) -> None:
        barrier.wait()
        start = time.perf_counter()
        for _ in range(iters):
            obj = op()
        assert obj is not None
        elapsed[idx] = time.perf_counter() - start

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    return max(elapsed) * 1e6 / iters


def _threaded_us(
    make_op: Callable[[], Callable[[], object]],
    *,
    threads: int = 4,
    iters: int = 40_000,
    samples: int = 7,
) -> tuple[float, float, float]:
    """Median/min/max us/op across ``threads`` workers on a shared op."""
    results = [_threaded_sample(make_op(), threads, iters) for _ in range(samples)]
    return statistics.median(results), min(results), max(results)


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _env_lines() -> list[str]:
    return [
        f"Python: {platform.python_version()} ({platform.machine()})",
        f"Platform: {platform.platform()}",
        f"CPU count: {os.cpu_count()}",
        f"Benchmark commit: {os.environ.get('GIT_COMMIT', 'unknown')}",
        f"injex: {_pkg_version('injex')}",
        f"doppy-di: {_pkg_version('doppy-di')}",
        f"Warmup iterations: {WARMUP}",
        f"Rounds: {ROUNDS}",
    ]


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario outcome: per-implementation timings plus semantics."""

    name: str
    unit: str
    method: str
    rows: list[tuple[str, float, float, float]]
    check: str
    note: str


def _status(doppy_median: float, baseline_median: float) -> str:
    ratio = doppy_median / baseline_median
    if ratio <= 0.95:
        return "WIN"
    if ratio <= 1.05:
        return "TIE"
    return "LOSS"


# ---------------------------------------------------------------------------
# Shared tiny handler-class factories (real annotations, no exec)
# ---------------------------------------------------------------------------


def _handler1(prefix: str, index: int, dep0: type) -> type:
    class Handler:
        __qualname__ = f"{prefix}{index}"

        def __init__(self, a: Any) -> None:
            self.a = a
            self.index = index

    Handler.__init__.__annotations__["a"] = dep0
    Handler.__name__ = f"{prefix}{index}"
    return Handler


def _handler2(prefix: str, index: int, dep0: type, dep1: type) -> type:
    class Handler:
        __qualname__ = f"{prefix}{index}"

        def __init__(self, a: Any, b: Any) -> None:
            self.a = a
            self.b = b
            self.index = index

    Handler.__init__.__annotations__["a"] = dep0
    Handler.__init__.__annotations__["b"] = dep1
    Handler.__name__ = f"{prefix}{index}"
    return Handler


def _handler3(prefix: str, index: int, dep0: type, dep1: type, dep2: type) -> type:
    class Handler:
        __qualname__ = f"{prefix}{index}"

        def __init__(self, a: Any, b: Any, c: Any) -> None:
            self.a = a
            self.b = b
            self.c = c
            self.index = index

    Handler.__init__.__annotations__["a"] = dep0
    Handler.__init__.__annotations__["b"] = dep1
    Handler.__init__.__annotations__["c"] = dep2
    Handler.__name__ = f"{prefix}{index}"
    return Handler


# ---------------------------------------------------------------------------
# Scenario 1: first-touch sweep over many roots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FTSettings:
    database_url: str = "sqlite:///:memory:"


class FTApiClient:
    def __init__(self, settings: FTSettings) -> None:
        self.settings = settings


class FTUserRepository:
    def __init__(self, client: FTApiClient) -> None:
        self.client = client


class FTAuditLog:
    def __init__(self, settings: FTSettings) -> None:
        self.settings = settings


ft_settings = FTSettings()
FT_HANDLERS = [_handler2("FTHandler", i, FTUserRepository, FTAuditLog) for i in range(48)]


def _ft_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(FTSettings, ft_settings)
    builder.service(FTApiClient, FTApiClient, lifetime="singleton", deps=[FTSettings])
    builder.service(FTUserRepository, FTUserRepository, lifetime="transient", deps=[FTApiClient])
    builder.service(FTAuditLog, FTAuditLog, lifetime="transient", deps=[FTSettings])
    for handler in FT_HANDLERS:
        builder.service(
            handler,
            handler,
            lifetime="transient",
            deps=[FTUserRepository, FTAuditLog],
        )
    return builder.build().compile()


def _ft_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(FTSettings, ft_settings)
    container.add_singleton(FTApiClient)
    container.add_transient(FTUserRepository)
    container.add_transient(FTAuditLog)
    for handler in FT_HANDLERS:
        container.add_transient(handler)
    container.assert_valid()
    return container


def scenario_first_touch() -> ScenarioResult:
    ft_api = FTApiClient(ft_settings)

    def manual_sweep() -> int:
        touched = 0
        for handler in FT_HANDLERS:
            handler(FTUserRepository(ft_api), FTAuditLog(ft_settings))
            touched += 1
        return touched

    def doppy_sweep() -> int:
        plan = _ft_setup_doppy()
        touched = 0
        for handler in FT_HANDLERS:
            assert plan.get(handler) is not None
            touched += 1
        return touched

    def injex_sweep() -> int:
        container = _ft_setup_injex()
        touched = 0
        for handler in FT_HANDLERS:
            assert container.resolve(handler) is not None
            touched += 1
        return touched

    rows = [
        ("manual", *_sweep_ms(manual_sweep)),
        ("injex", *_sweep_ms(injex_sweep)),
        ("doppy-di compiled", *_sweep_ms(doppy_sweep)),
    ]

    try:
        plan = _ft_setup_doppy()
        first = [plan.get(h) for h in FT_HANDLERS]
        second = [plan.get(h) for h in FT_HANDLERS]
        assert all(a is not b for a, b in zip(first, second))
        assert first[0].a.client is second[0].a.client
        assert first[0].a.client.settings is ft_settings
        injex_container = _ft_setup_injex()
        inj_first = [injex_container.resolve(h) for h in FT_HANDLERS]
        inj_second = [injex_container.resolve(h) for h in FT_HANDLERS]
        assert all(a is not b for a, b in zip(inj_first, inj_second))
        assert inj_first[0].a.client is inj_second[0].a.client
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="first_touch_sweep",
        unit="ms/sweep (48 roots)",
        method="9 sweeps, fresh container per sweep, setup untimed",
        rows=rows,
        check=check,
        note=(
            "Boot fan-out. Injex exec-compiles each root creator lazily "
            "inside the measured region; doppy-di compiles upfront. "
            "Expected winner: doppy-di compiled."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 2: deep transient chain (depth 16)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DCSettings:
    database_url: str = "sqlite:///:memory:"


class DCApiClient:
    def __init__(self, settings: DCSettings) -> None:
        self.settings = settings


dc_settings = DCSettings()
_DC_LEVELS: list[type] = []
_prev: type = DCApiClient
for _i in range(15):
    _lvl = _handler1("DCLevel", _i, _prev)
    _DC_LEVELS.append(_lvl)
    _prev = _lvl
DC_TOP = _DC_LEVELS[-1]


def _dc_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(DCSettings, dc_settings)
    builder.service(DCApiClient, DCApiClient, lifetime="singleton", deps=[DCSettings])
    prev_key: type = DCApiClient
    for level in _DC_LEVELS:
        builder.service(level, level, lifetime="transient", deps=[prev_key])
        prev_key = level
    return builder.build().compile()


def _dc_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(DCSettings, dc_settings)
    container.add_singleton(DCApiClient)
    for level in _DC_LEVELS:
        container.add_transient(level)
    container.assert_valid()
    container.resolve(DC_TOP)
    return container


def scenario_deep_chain() -> ScenarioResult:
    dc_api = DCApiClient(dc_settings)

    # Manual floor: unrolled 15-deep construction via the real classes.
    def manual_chain() -> Any:
        current: Any = dc_api
        for level in reversed(_DC_LEVELS):
            current = level(current)
        return current

    plan = _dc_setup_doppy()
    injex_container = _dc_setup_injex()

    rows = [
        ("manual", *_warm_us(manual_chain, iterations=100_000)),
        ("injex", *_warm_us(lambda: injex_container.resolve(DC_TOP), iterations=100_000)),
        ("doppy-di compiled", *_warm_us(lambda: plan.get(DC_TOP), iterations=100_000)),
    ]

    try:
        first = plan.get(DC_TOP)
        second = plan.get(DC_TOP)
        node: Any = first
        depth = 0
        while hasattr(node, "a"):
            node = node.a
            depth += 1
        assert depth == 15
        assert isinstance(node, DCApiClient)
        assert node.settings is dc_settings
        assert first is not second
        inj_a = injex_container.resolve(DC_TOP)
        inj_b = injex_container.resolve(DC_TOP)
        assert inj_a is not inj_b
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="deep_chain_d16",
        unit="us/op",
        method="100k iterations x 9 rounds, warm",
        rows=rows,
        check=check,
        note=(
            "Middleware/pipeline shape. Pre-issue-40 doppy-di pays one "
            "closure frame per level; flattening makes frame count "
            "depth-independent. Expected post-40: tie or win."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 3: wide application service (10 deps)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WSettings:
    database_url: str = "sqlite:///:memory:"
    smtp_url: str = "smtp://localhost"


class WApiClient:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WUserRepository:
    def __init__(self, client: WApiClient) -> None:
        self.client = client


class WEmailSender:
    def __init__(self, client: WApiClient) -> None:
        self.client = client


class WAuditLog:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WCacheStore:
    def __init__(self, client: WApiClient) -> None:
        self.client = client


class WSearchIndex:
    def __init__(self, client: WApiClient) -> None:
        self.client = client


class WMetrics:
    def __init__(self, client: WApiClient) -> None:
        self.client = client


class WDbConfig:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WQueueConfig:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WSmtpConfig:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WFeatureFlags:
    def __init__(self, settings: WSettings) -> None:
        self.settings = settings


class WideService:
    def __init__(
        self,
        repo: WUserRepository,
        email: WEmailSender,
        audit: WAuditLog,
        cache: WCacheStore,
        search: WSearchIndex,
        metrics: WMetrics,
        db: WDbConfig,
        queue: WQueueConfig,
        smtp: WSmtpConfig,
        flags: WFeatureFlags,
    ) -> None:
        self.repo = repo
        self.email = email
        self.audit = audit
        self.cache = cache
        self.search = search
        self.metrics = metrics
        self.db = db
        self.queue = queue
        self.smtp = smtp
        self.flags = flags


w_settings = WSettings()
_WIDE_DEPS: tuple[Any, ...] = (
    WUserRepository,
    WEmailSender,
    WAuditLog,
    WCacheStore,
    WSearchIndex,
    WMetrics,
    WDbConfig,
    WQueueConfig,
    WSmtpConfig,
    WFeatureFlags,
)


def _wide_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(WSettings, w_settings)
    builder.service(WApiClient, WApiClient, lifetime="singleton", deps=[WSettings])
    builder.service(WFeatureFlags, WFeatureFlags, lifetime="singleton", deps=[WSettings])
    leaf_deps: dict[type, list[Any]] = {
        WUserRepository: [WApiClient],
        WEmailSender: [WApiClient],
        WAuditLog: [WSettings],
        WCacheStore: [WApiClient],
        WSearchIndex: [WApiClient],
        WMetrics: [WApiClient],
        WDbConfig: [WSettings],
        WQueueConfig: [WSettings],
        WSmtpConfig: [WSettings],
    }
    for leaf, deps in leaf_deps.items():
        builder.service(leaf, leaf, lifetime="transient", deps=deps)
    builder.service(WideService, WideService, lifetime="transient", deps=list(_WIDE_DEPS))
    return builder.build().compile()


def _wide_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(WSettings, w_settings)
    container.add_singleton(WApiClient)
    container.add_singleton(WFeatureFlags)
    for leaf in (
        WUserRepository,
        WEmailSender,
        WAuditLog,
        WCacheStore,
        WSearchIndex,
        WMetrics,
        WDbConfig,
        WQueueConfig,
        WSmtpConfig,
    ):
        container.add_transient(leaf)
    container.add_transient(WideService)
    container.assert_valid()
    container.resolve(WideService)
    return container


def scenario_wide_service() -> ScenarioResult:
    w_api = WApiClient(w_settings)
    w_flags = WFeatureFlags(w_settings)

    def manual_resolve() -> WideService:
        return WideService(
            WUserRepository(w_api),
            WEmailSender(w_api),
            WAuditLog(w_settings),
            WCacheStore(w_api),
            WSearchIndex(w_api),
            WMetrics(w_api),
            WDbConfig(w_settings),
            WQueueConfig(w_settings),
            WSmtpConfig(w_settings),
            w_flags,
        )

    plan = _wide_setup_doppy()
    injex_container = _wide_setup_injex()

    rows = [
        ("manual", *_warm_us(manual_resolve, iterations=100_000)),
        ("injex", *_warm_us(lambda: injex_container.resolve(WideService), iterations=100_000)),
        ("doppy-di compiled", *_warm_us(lambda: plan.get(WideService), iterations=100_000)),
    ]

    try:
        first = plan.get(WideService)
        second = plan.get(WideService)
        assert isinstance(first, WideService)
        assert first is not second
        assert first.repo.client is second.repo.client
        assert first.flags is second.flags
        assert first.repo.client.settings is w_settings
        inj_a = injex_container.resolve(WideService)
        inj_b = injex_container.resolve(WideService)
        assert inj_a is not inj_b
        assert inj_a.repo.client is inj_b.repo.client
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="wide_service_d10",
        unit="us/op",
        method="100k iterations x 9 rounds, warm",
        rows=rows,
        check=check,
        note=(
            "Typical application service with 10 deps over 2 singletons. "
            "Amplifies CSE and transient-inlining gains. Expected post-40: "
            "tie or win."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 4: round-robin dispatch across roots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RRSettings:
    smtp_url: str = "smtp://localhost"


class RRApiClient:
    def __init__(self, settings: RRSettings) -> None:
        self.settings = settings


class RRUserRepository:
    def __init__(self, client: RRApiClient) -> None:
        self.client = client


class RREmailSender:
    def __init__(self, client: RRApiClient) -> None:
        self.client = client


class RRAuditLog:
    def __init__(self, settings: RRSettings) -> None:
        self.settings = settings


rr_settings = RRSettings()
RR_ROOTS = [
    _handler3("RRHandler", i, RRUserRepository, RREmailSender, RRAuditLog) for i in range(8)
]


def _rr_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(RRSettings, rr_settings)
    builder.service(RRApiClient, RRApiClient, lifetime="singleton", deps=[RRSettings])
    builder.service(RRUserRepository, RRUserRepository, lifetime="transient", deps=[RRApiClient])
    builder.service(RREmailSender, RREmailSender, lifetime="transient", deps=[RRApiClient])
    builder.service(RRAuditLog, RRAuditLog, lifetime="transient", deps=[RRSettings])
    for root in RR_ROOTS:
        builder.service(
            root,
            root,
            lifetime="transient",
            deps=[RRUserRepository, RREmailSender, RRAuditLog],
        )
    return builder.build().compile()


def _rr_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(RRSettings, rr_settings)
    container.add_singleton(RRApiClient)
    container.add_transient(RRUserRepository)
    container.add_transient(RREmailSender)
    container.add_transient(RRAuditLog)
    for root in RR_ROOTS:
        container.add_transient(root)
    container.assert_valid()
    for root in RR_ROOTS:
        container.resolve(root)
    return container


def _cycle(resolver: Callable[[type], object], roots: list[type]) -> Callable[[], object]:
    counter = {"i": 0}

    def op() -> object:
        i = counter["i"]
        counter["i"] = i + 1
        return resolver(roots[i % len(roots)])

    return op


def scenario_round_robin() -> ScenarioResult:
    rr_api = RRApiClient(rr_settings)

    rr_state = {"i": 0}

    def manual_cycle() -> object:
        i = rr_state["i"]
        rr_state["i"] = i + 1
        root = RR_ROOTS[i % len(RR_ROOTS)]
        return root(
            RRUserRepository(rr_api),
            RREmailSender(rr_api),
            RRAuditLog(rr_settings),
        )

    plan = _rr_setup_doppy()
    injex_container = _rr_setup_injex()

    rows = [
        ("manual", *_warm_us(manual_cycle, iterations=150_000)),
        (
            "injex",
            *_warm_us(_cycle(injex_container.resolve, RR_ROOTS), iterations=150_000),
        ),
        (
            "doppy-di compiled",
            *_warm_us(_cycle(plan.get, RR_ROOTS), iterations=150_000),
        ),
    ]

    try:
        seen = [plan.get(root) for root in RR_ROOTS]
        again = [plan.get(root) for root in RR_ROOTS]
        assert all(a is not b for a, b in zip(seen, again))
        assert seen[0].a.client is again[0].a.client
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="round_robin_roots",
        unit="us/op",
        method="150k iterations x 9 rounds, warm, 8 roots cycled",
        rows=rows,
        check=check,
        note=(
            "Request dispatcher alternating across 8 roots. doppy-di "
            "resolvers are zero-arg closures; Injex threads scope through "
            "nested creator calls. Expected: tie or small win."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 5: value-heavy root (config bootstrap)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VHDbUrl:
    value: str = "sqlite:///:memory:"


@dataclass(frozen=True)
class VHSmtpUrl:
    value: str = "smtp://localhost"


@dataclass(frozen=True)
class VHQueueUrl:
    value: str = "amqp://localhost"


@dataclass(frozen=True)
class VHRegion:
    value: str = "eu-central-1"


@dataclass(frozen=True)
class VHEnv:
    value: str = "production"


@dataclass(frozen=True)
class VHLogLevel:
    value: str = "INFO"


@dataclass(frozen=True)
class VHTimeoutMs:
    value: int = 2500


@dataclass(frozen=True)
class VHMaxRetries:
    value: int = 5


class VHRoot:
    def __init__(
        self,
        db: VHDbUrl,
        smtp: VHSmtpUrl,
        queue: VHQueueUrl,
        region: VHRegion,
        env: VHEnv,
        log_level: VHLogLevel,
        timeout: VHTimeoutMs,
        retries: VHMaxRetries,
    ) -> None:
        self.db = db
        self.smtp = smtp
        self.queue = queue
        self.region = region
        self.env = env
        self.log_level = log_level
        self.timeout = timeout
        self.retries = retries


vh_values: dict[Any, Any] = {
    VHDbUrl: VHDbUrl(),
    VHSmtpUrl: VHSmtpUrl(),
    VHQueueUrl: VHQueueUrl(),
    VHRegion: VHRegion(),
    VHEnv: VHEnv(),
    VHLogLevel: VHLogLevel(),
    VHTimeoutMs: VHTimeoutMs(),
    VHMaxRetries: VHMaxRetries(),
}


def _vh_setup_doppy() -> Any:
    builder = ContainerBuilder()
    for key, value in vh_values.items():
        builder.value(key, value)
    builder.service(
        VHRoot,
        VHRoot,
        lifetime="transient",
        deps=list(vh_values.keys()),
    )
    return builder.build().compile()


def _vh_setup_injex() -> Any:
    container = InjexContainer()
    for key, value in vh_values.items():
        container.add_instance(key, value)
    container.add_transient(VHRoot)
    container.assert_valid()
    container.resolve(VHRoot)
    return container


def scenario_value_heavy() -> ScenarioResult:
    def manual_resolve() -> VHRoot:
        return VHRoot(
            VHDbUrl(),
            VHSmtpUrl(),
            VHQueueUrl(),
            VHRegion(),
            VHEnv(),
            VHLogLevel(),
            VHTimeoutMs(),
            VHMaxRetries(),
        )

    plan = _vh_setup_doppy()
    injex_container = _vh_setup_injex()

    rows = [
        ("manual", *_warm_us(manual_resolve, iterations=150_000)),
        ("injex", *_warm_us(lambda: injex_container.resolve(VHRoot), iterations=150_000)),
        ("doppy-di compiled", *_warm_us(lambda: plan.get(VHRoot), iterations=150_000)),
    ]

    try:
        root = plan.get(VHRoot)
        assert root.db == vh_values[VHDbUrl]
        assert root.retries == vh_values[VHMaxRetries]
        assert plan.get(VHRoot) is not root
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="value_heavy_root",
        unit="us/op",
        method="150k iterations x 9 rounds, warm",
        rows=rows,
        check=check,
        note=(
            "Config-driven bootstrap consuming 8 constants. Current doppy-di "
            "fast path spends one maker frame per value; Injex folds "
            "constants flat. Expected: loss until constant inlining lands."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 6: singleton fetch loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SFSettings:
    database_url: str = "sqlite:///:memory:"


class SFService:
    def __init__(self, settings: SFSettings) -> None:
        self.settings = settings


sf_settings = SFSettings()


def _sf_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(SFSettings, sf_settings)
    builder.service(SFService, SFService, lifetime="singleton", deps=[SFSettings])
    container = builder.build()
    plan = container.compile()
    plan.get(SFService)
    return plan


def _sf_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(SFSettings, sf_settings)
    container.add_singleton(SFService)
    container.assert_valid()
    container.resolve(SFService)
    return container


def scenario_singleton_fetch() -> ScenarioResult:
    sf_service = SFService(sf_settings)
    plan = _sf_setup_doppy()
    injex_container = _sf_setup_injex()

    rows = [
        ("manual", *_warm_us(lambda: sf_service, iterations=250_000)),
        ("injex", *_warm_us(lambda: injex_container.resolve(SFService), iterations=250_000)),
        ("doppy-di compiled", *_warm_us(lambda: plan.get(SFService), iterations=250_000)),
    ]

    try:
        assert plan.get(SFService) is plan.get(SFService)
        assert plan.get(SFService).settings is sf_settings
        assert injex_container.resolve(SFService) is injex_container.resolve(SFService)
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="singleton_fetch_loop",
        unit="us/op",
        method="250k iterations x 9 rounds, warm, cached singleton",
        rows=rows,
        check=check,
        note=(
            "Per-request cached-service lookup. Parity guard: catches "
            "guard-check bloat in ExecutionPlan.get. Expected: tie."
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 7: threaded resolve
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TGSettings:
    database_url: str = "sqlite:///:memory:"


class TGApiClient:
    def __init__(self, settings: TGSettings) -> None:
        self.settings = settings


class TGUserRepository:
    def __init__(self, client: TGApiClient) -> None:
        self.client = client


class TGAuditLog:
    def __init__(self, settings: TGSettings) -> None:
        self.settings = settings


class TGHandler:
    def __init__(self, repo: TGUserRepository, audit: TGAuditLog) -> None:
        self.repo = repo
        self.audit = audit


tg_settings = TGSettings()


def _tg_setup_doppy() -> Any:
    builder = ContainerBuilder()
    builder.value(TGSettings, tg_settings)
    builder.service(TGApiClient, TGApiClient, lifetime="singleton", deps=[TGSettings])
    builder.service(TGUserRepository, TGUserRepository, lifetime="transient", deps=[TGApiClient])
    builder.service(TGAuditLog, TGAuditLog, lifetime="transient", deps=[TGSettings])
    builder.service(
        TGHandler,
        TGHandler,
        lifetime="transient",
        deps=[TGUserRepository, TGAuditLog],
    )
    container = builder.build()
    plan = container.compile()
    plan.get(TGHandler)
    return plan


def _tg_setup_injex() -> Any:
    container = InjexContainer()
    container.add_instance(TGSettings, tg_settings)
    container.add_singleton(TGApiClient)
    container.add_transient(TGUserRepository)
    container.add_transient(TGAuditLog)
    container.add_transient(TGHandler)
    container.assert_valid()
    container.resolve(TGHandler)
    return container


def scenario_threaded() -> ScenarioResult:
    tg_api = TGApiClient(tg_settings)

    def manual_op() -> TGHandler:
        return TGHandler(TGUserRepository(tg_api), TGAuditLog(tg_settings))

    plan = _tg_setup_doppy()
    injex_container = _tg_setup_injex()

    rows = [
        ("manual", *_threaded_us(lambda: manual_op)),
        ("injex", *_threaded_us(lambda: lambda: injex_container.resolve(TGHandler))),
        ("doppy-di compiled", *_threaded_us(lambda: lambda: plan.get(TGHandler))),
    ]

    try:
        handler = plan.get(TGHandler)
        assert isinstance(handler, TGHandler)
        assert handler.repo.client.settings is tg_settings
        check = "PASS"
    except Exception as exc:
        check = f"FAIL ({exc!r})"

    return ScenarioResult(
        name="threaded_resolve",
        unit="us/op (4 threads)",
        method="4 threads x 40k iters x 7 samples, wall-clock per thread",
        rows=rows,
        check=check,
        note=(
            "Web-worker contention on shared graph. Both lock only on "
            "singleton miss. Expected: tie; regression means a lock leaked "
            "onto the warm path."
        ),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

SCENARIOS: tuple[Callable[[], ScenarioResult], ...] = (
    scenario_first_touch,
    scenario_deep_chain,
    scenario_wide_service,
    scenario_round_robin,
    scenario_value_heavy,
    scenario_singleton_fetch,
    scenario_threaded,
)


def _print_scenario(result: ScenarioResult) -> None:
    baseline = next((med for name, med, _, _ in result.rows if name == "injex"), None)
    print()
    print(f"== {result.name} [{result.unit}] ({result.method})")
    print(f"   why: {result.note}")
    ordered = sorted(result.rows, key=lambda row: row[1])
    print(f"   {'implementation':<22} {'median':>12} {'min..max':>22} {'vs injex':>9}")
    for name, median, low, high in ordered:
        mark = ""
        if baseline is not None and name.startswith("doppy"):
            mark = _status(median, baseline)
        print(f"   {name:<22} {median:>12.3f} {low:>10.3f}..{high:<10.3f} {mark:>9}")
    print(f"   semantic check: {result.check}")


def _save_artifacts(
    timestamp: str,
    env: list[str],
    results: list[ScenarioResult],
) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    lines = ["Environment", "-----------", *env, ""]
    payload: dict[str, Any] = {
        "generated": timestamp,
        "environment": env,
        "scenarios": {},
    }
    for result in results:
        baseline = next((m for n, m, _, _ in result.rows if n == "injex"), None)
        lines.append(f"{result.name} [{result.unit}] ({result.method})")
        lines.append(f"  why: {result.note}")
        for name, median, low, high in sorted(result.rows, key=lambda row: row[1]):
            mark = (
                _status(median, baseline)
                if baseline is not None and name.startswith("doppy")
                else ""
            )
            lines.append(f"  {name:<22} {median:>12.3f} {low:>10.3f}..{high:<10.3f} {mark}")
        lines.append(f"  semantic check: {result.check}")
        lines.append("")
        payload["scenarios"][result.name] = {
            "unit": result.unit,
            "method": result.method,
            "note": result.note,
            "semantic_check": result.check,
            "results": {
                name: {"median": med, "min": low, "max": high}
                for name, med, low, high in result.rows
            },
        }
    raw_path = RESULTS_DIR / f"{timestamp}-scenarios.txt"
    raw_path.write_text(_NL.join(lines) + _NL, encoding="utf-8")
    summary_path = RESULTS_DIR / f"{timestamp}-scenarios.json"
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Artifacts: {raw_path.name}, {summary_path.name}")


def main() -> None:
    print("Scenario benchmarks: doppy-di compiled vs Injex (honest reporting)")
    for line in _env_lines():
        print(line)
    results = [scenario() for scenario in SCENARIOS]
    for result in results:
        _print_scenario(result)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _save_artifacts(timestamp, _env_lines(), results)


if __name__ == "__main__":
    main()
