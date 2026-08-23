"""Statistical comparison of injex vs doppy-di compiled paths (issue #42).

Runs many interleaved measurement rounds, then answers three questions with
proper statistics instead of eyeballing medians:

1. Is injex significantly faster than doppy-di compiled (allow/frozen)?
   -> Mann-Whitney U test over per-round samples + bootstrap 95% CI for the
   median difference. "Not significant" requires p >= 0.05 AND a CI that
   includes zero.
2. How much do the distributions overlap?
   -> Per-round ratio distributions and common-language effect size.
3. Which implementation is more stable?
   -> Coefficient of variation (stdev/mean) of per-round times.

No cherry-picking: every collected round participates in the statistics.
"""

from __future__ import annotations

import gc
import math
import random
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from injex import Container as InjexContainer

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


def setup_doppy_di_compiled() -> Callable[[], RegisterUser]:
    container = _build_doppy_di()
    plan = container.compile(allow_post_compile_overrides=True)
    plan.get(RegisterUser)
    return lambda: plan.get(RegisterUser)


def setup_doppy_di_frozen() -> Callable[[], RegisterUser]:
    container = _build_doppy_di()
    plan = container.compile(allow_post_compile_overrides=False)
    plan.get(RegisterUser)
    return lambda: plan.get(RegisterUser)


def bench_interleaved(
    cases: Sequence[tuple[str, Callable[[], object]]],
    *,
    iterations: int,
    rounds: int,
    warmup: int = 12_000,
) -> dict[str, list[float]]:
    """Return per-round ns/op samples, interleaved A/B/A/B..., GC disabled."""
    for _, fn in cases:
        for _ in range(warmup):
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
                samples[name].append(float(end - start) / iterations)
    finally:
        if gc_was_enabled:
            gc.enable()
    return samples


def mann_whitney_p(xs: list[float], ys: list[float]) -> float:
    """Two-sided Mann-Whitney U p-value (normal approximation, ties corrected)."""
    n1, n2 = len(xs), len(ys)
    combined: list[tuple[float, int]] = [(v, 0) for v in xs] + [(v, 1) for v in ys]
    combined.sort(key=lambda pair: pair[0])

    ranks = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j - 1) / 2 + 1
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j

    r1 = sum(ranks[k] for k in range(len(combined)) if combined[k][1] == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2

    tie_counts: dict[float, int] = {}
    for value, _ in combined:
        tie_counts[value] = tie_counts.get(value, 0) + 1
    tie_sum = sum(c**3 - c for c in tie_counts.values())
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - tie_sum / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (u1 - mu) / sigma
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def bootstrap_median_diff_ci(
    xs: list[float],
    ys: list[float],
    *,
    boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap CI for median(xs) - median(ys), in ns/op."""
    rng = random.Random(seed)

    def resampled_median(values: list[float]) -> float:
        draw: Iterator[float] = (rng.choice(values) for _ in values)
        return statistics.median(draw)

    diffs = sorted(resampled_median(xs) - resampled_median(ys) for _ in range(boot))
    lo_idx = int((alpha / 2) * boot)
    hi_idx = int((1 - alpha / 2) * boot) - 1
    return diffs[lo_idx], diffs[hi_idx]


def common_language_effect(xs: list[float], ys: list[float]) -> float:
    """P(random x sample < random y sample). 0.5 = full overlap."""
    wins = sum(1 for x in xs for y in ys if x < y)
    return wins / (len(xs) * len(ys))


def coefficient_of_variation(samples: list[float]) -> float:
    mean = statistics.mean(samples)
    stdev = statistics.stdev(samples)
    return stdev / mean * 100.0


def pairwise_report(
    label: str,
    xs: list[float],
    ys: list[float],
) -> str:
    """Compare xs vs ys: diff CI, MWU p-value, overlap, per-round ratios."""
    med_x = statistics.median(xs)
    med_y = statistics.median(ys)
    lo, hi = bootstrap_median_diff_ci(xs, ys)
    p = mann_whitney_p(xs, ys)
    cle = common_language_effect(xs, ys)
    ratios = sorted(x / y for x, y in zip(xs, ys))
    med_ratio = statistics.median(ratios)

    significant = p < 0.05 and (lo > 0 or hi < 0)
    verdict = "SIGNIFICANT" if significant else "not significant"

    return (
        f"{label}\n"
        f"  median diff: {(med_x - med_y) / 1000:+.3f} us/op "
        f"(x {med_ratio:.3f} of y)\n"
        f"  bootstrap 95% CI of diff: [{lo / 1000:+.3f}, {hi / 1000:+.3f}] us/op\n"
        f"  Mann-Whitney U p-value: {p:.4f}\n"
        f"  P(x < y) per round: {cle:.2f}\n"
        f"  verdict: {verdict} at alpha=0.05"
    )


def main() -> None:
    rounds = 31
    iterations = 100_000

    cases = [
        ("manual", manual_resolve),
        ("injex", setup_injex()),
        ("doppy-di compiled", setup_doppy_di_compiled()),
        ("doppy-di frozen", setup_doppy_di_frozen()),
    ]
    samples = bench_interleaved(cases, iterations=iterations, rounds=rounds)

    print(f"rounds={rounds} iterations={iterations} interleaved A/B, gc disabled\n")

    print("Per-library stability")
    print(f"{'library':<22} {'median us/op':>14} {'min':>8} {'max':>8} {'CV %':>7}")
    for name, vals in samples.items():
        print(
            f"{name:<22} {statistics.median(vals) / 1000:>14.3f} "
            f"{min(vals) / 1000:>8.3f} {max(vals) / 1000:>8.3f} "
            f"{coefficient_of_variation(vals):>7.1f}"
        )

    print("\nPairwise comparisons (x vs y)")
    comparisons = [
        ("injex vs doppy-di frozen", samples["injex"], samples["doppy-di frozen"]),
        ("injex vs doppy-di compiled", samples["injex"], samples["doppy-di compiled"]),
        (
            "doppy-di frozen vs compiled",
            samples["doppy-di frozen"],
            samples["doppy-di compiled"],
        ),
        ("manual vs injex", samples["manual"], samples["injex"]),
    ]
    for label, xs, ys in comparisons:
        print(pairwise_report(label, xs, ys))
        print()


if __name__ == "__main__":
    main()
