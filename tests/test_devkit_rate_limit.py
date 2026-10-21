"""Tests for RateLimitedContainer and unwrap guard."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from doppy_di import ContainerBuilder, LoggingContainer, ValidatingContainer
from doppy_di.devkit import (
    RateLimitedContainer,
    ResolutionRateLimitExceededError,
    ensure_unwrapped,
    unwrap,
)


def make_base() -> object:
    builder = ContainerBuilder()
    builder.value("config", {"debug": True})
    builder.service("svc", lambda: object(), lifetime="transient")
    builder.value("x", 1)
    return builder.build()


def test_normal_usage_under_limit() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(base, max_resolutions_per_second=100, window_seconds=1.0)  # type: ignore[arg-type]
    assert wrapped.get("x") == 1  # type: ignore[no-untyped-call]


def test_warn_mode_continues(caplog: pytest.LogCaptureFixture) -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=2, window_seconds=1.0, on_limit="warn"
    )  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="doppy_di.devkit.rate_limit"):
        for _ in range(5):
            assert wrapped.get("svc") is not None  # type: ignore[no-untyped-call]
    assert any("rate limit" in r.message.lower() for r in caplog.records)


def test_raise_mode_boundary() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=2, window_seconds=1.0, on_limit="raise"
    )  # type: ignore[arg-type]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    with pytest.raises(ResolutionRateLimitExceededError):
        wrapped.get("svc")  # type: ignore[no-untyped-call]


def test_callback_mode() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    calls: list[tuple[object, int, float]] = []
    wrapped = RateLimitedContainer(  # type: ignore[arg-type]
        base,
        max_resolutions_per_second=1,
        window_seconds=1.0,
        on_limit="callback",
        on_limit_callback=lambda k, c, w: calls.append((k, c, w)),
    )
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    assert len(calls) == 1
    assert calls[0][0] == "svc"


def test_ignore_keys() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(  # type: ignore[arg-type]
        base,
        max_resolutions_per_second=1,
        window_seconds=1.0,
        on_limit="raise",
        ignore_keys={"svc"},
    )
    for _ in range(5):
        wrapped.get("svc")  # type: ignore[no-untyped-call]


def test_cache_hits_excluded() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=1, window_seconds=1.0, on_limit="raise"
    )  # type: ignore[arg-type]
    wrapped.get("x")  # type: ignore[no-untyped-call]
    assert wrapped.get("x") == 1  # type: ignore[no-untyped-call]


def test_usage_report_top_keys() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(base, max_resolutions_per_second=1000, window_seconds=1.0)  # type: ignore[arg-type]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    wrapped.get("x")  # type: ignore[no-untyped-call]
    report = wrapped.usage_report(top_n=2)
    assert report[0][0] == "'svc'"
    assert report[0][1] == 2


def test_async_usage() -> None:
    async def main() -> None:
        builder = ContainerBuilder()
        builder.value("x", 1)
        base = builder.build()  # type: ignore[no-untyped-call]
        wrapped = RateLimitedContainer(base, max_resolutions_per_second=10, window_seconds=60.0)  # type: ignore[arg-type]
        assert await wrapped.aget("x") == 1  # type: ignore[no-untyped-call]

    asyncio.run(main())


def test_thread_safety() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(base, max_resolutions_per_second=10000, window_seconds=60.0)  # type: ignore[arg-type]
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(50):
                wrapped.get("svc")  # type: ignore[no-untyped-call]
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_unwrap_detector() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    w1 = LoggingContainer(base, lambda _m: None)  # type: ignore[arg-type]
    w2 = RateLimitedContainer(w1, max_resolutions_per_second=100)  # type: ignore[arg-type]
    assert unwrap(w2) is base  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        ensure_unwrapped(w2, mode="raise")
    assert ensure_unwrapped(base) is base


def test_backfill_aget() -> None:
    async def main() -> None:
        builder = ContainerBuilder()
        builder.value("x", 1)
        base = builder.build()  # type: ignore[no-untyped-call]
        lw = LoggingContainer(base, lambda _m: None)  # type: ignore[arg-type]
        assert await lw.aget("x") == 1  # type: ignore[no-untyped-call]
        from doppy_di.devkit import UnorderedPolicy

        vw = ValidatingContainer(base, UnorderedPolicy())  # type: ignore[arg-type]
        assert await vw.aget("x") == 1  # type: ignore[no-untyped-call]

    asyncio.run(main())


def test_invalid_modes() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    with pytest.raises(ValueError, match="on_limit"):
        RateLimitedContainer(base, on_limit="block")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="callback"):
        RateLimitedContainer(base, on_limit="callback")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="report_mode"):
        RateLimitedContainer(base, report_mode="all")  # type: ignore[arg-type]


def test_count_cache_hits_opt_in() -> None:
    builder = ContainerBuilder()
    builder.value("x", 1)
    base = builder.build()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base,
        max_resolutions_per_second=1,
        window_seconds=1.0,
        on_limit="raise",
        count_cache_hits=True,
    )  # type: ignore[arg-type]
    wrapped.get("x")  # type: ignore[no-untyped-call]
    with pytest.raises(ResolutionRateLimitExceededError):
        wrapped.get("x")  # type: ignore[no-untyped-call]


def test_delegation_and_report_modes() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=100, window_seconds=1.0, report_mode="cumulative"
    )  # type: ignore[arg-type]
    assert wrapped.has("x") is True  # type: ignore[no-untyped-call]
    assert wrapped.scope("req") is not None  # type: ignore[no-untyped-call]
    with wrapped.override("x", 2):  # type: ignore[no-untyped-call]
        assert wrapped.get("x") == 2  # type: ignore[no-untyped-call]
    assert wrapped.wrapper_depth() == 1
    rep = wrapped.usage_report(top_n=5)
    assert rep[0][1] >= 1
    assert ensure_unwrapped(wrapped, mode="warn") is wrapped


def test_async_raise() -> None:
    async def main() -> None:
        builder = ContainerBuilder()
        builder.service("s", lambda: object(), lifetime="transient")
        base = builder.build()  # type: ignore[no-untyped-call]
        wrapped = RateLimitedContainer(
            base, max_resolutions_per_second=1, window_seconds=1.0, on_limit="raise"
        )  # type: ignore[arg-type]
        await wrapped.aget("s")  # type: ignore[no-untyped-call]
        with pytest.raises(ResolutionRateLimitExceededError):
            await wrapped.aget("s")  # type: ignore[no-untyped-call]

    asyncio.run(main())


def test_error_attrs_and_has_scope() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=1, window_seconds=1.0, on_limit="raise"
    )  # type: ignore[arg-type]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    with pytest.raises(ResolutionRateLimitExceededError, match="svc") as exc_info:
        wrapped.get("svc")  # type: ignore[no-untyped-call]
    assert exc_info.value.key == "svc"
    assert exc_info.value.count == 2
    assert wrapped.has("missing") is False  # type: ignore[no-untyped-call]
    assert wrapped.scope("s2") is not None  # type: ignore[no-untyped-call]


def test_unhashable_key_no_crash() -> None:
    class Bad:
        __hash__ = None  # type: ignore[assignment]

    builder = ContainerBuilder()
    builder.value("ok", 1)
    base = builder.build()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(base, max_resolutions_per_second=100)  # type: ignore[arg-type]
    assert wrapped._is_cache_hit(Bad()) is False  # type: ignore[arg-type]


def test_unwrap_chain_depth() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    w1 = LoggingContainer(base, lambda _m: None)  # type: ignore[arg-type]
    w2 = RateLimitedContainer(w1, max_resolutions_per_second=100)  # type: ignore[arg-type]
    assert w2.wrapper_depth() == 2
    assert unwrap(w2) is base  # type: ignore[arg-type]


def test_aget_fallback_sync_only() -> None:
    import asyncio as _aio

    class SyncOnly:
        _devkit = True

        def __init__(self, inner: object) -> None:
            self.wrapped = inner

        def get(self, key: object) -> object:
            return self.wrapped.get(key)  # type: ignore[no-untyped-call,union-attr]

        def has(self, key: object) -> bool:
            return True

        def scope(self, name: str) -> object:
            return self.wrapped.scope(name)  # type: ignore[no-untyped-call,union-attr,no-any-return]

    async def main() -> None:
        builder = ContainerBuilder()
        builder.value("x", 5)
        base = builder.build()  # type: ignore[no-untyped-call]
        mid = SyncOnly(base)
        wrapped = RateLimitedContainer(mid, max_resolutions_per_second=100)  # type: ignore[arg-type]
        assert await wrapped.aget("x") == 5  # type: ignore[no-untyped-call]
        assert wrapped.scope("q") is not None  # type: ignore[no-untyped-call]

    _aio.run(main())


def test_sliding_window_expiry() -> None:
    base = make_base()  # type: ignore[no-untyped-call]
    wrapped = RateLimitedContainer(
        base, max_resolutions_per_second=1, window_seconds=0.05, on_limit="raise"
    )  # type: ignore[arg-type]
    wrapped.get("svc")  # type: ignore[no-untyped-call]
    time.sleep(0.07)
    wrapped.get("svc")  # type: ignore[no-untyped-call]
