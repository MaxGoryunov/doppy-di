"""Benchmark: bare Container.get vs RateLimitedContainer overhead."""

from __future__ import annotations

import time

from doppy_di import ContainerBuilder
from doppy_di.devkit import LoggingContainer, RateLimitedContainer


def build_base():  # type: ignore[no-untyped-def]
    builder = ContainerBuilder()
    builder.service("svc", lambda: object(), lifetime="transient")
    return builder.build()


def bench(container, n: int = 5000) -> float:  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    for _ in range(n):
        container.get("svc")
    return time.perf_counter() - start


def main() -> None:
    bare = build_base()
    limited = RateLimitedContainer(build_base(), max_resolutions_per_second=10**9)
    chained = RateLimitedContainer(
        LoggingContainer(build_base(), lambda _m: None), max_resolutions_per_second=10**9
    )
    n = 5000
    t_bare = bench(bare, n)
    t_lim = bench(limited, n)
    t_chain = bench(chained, n)
    print(f"bare: {t_bare:.4f}s / wrapped: {t_lim:.4f}s / chain3: {t_chain:.4f}s for {n} gets")
    print(f"overhead per get: {(t_lim - t_bare) / n * 1e6:.2f}us")


if __name__ == "__main__":
    main()
