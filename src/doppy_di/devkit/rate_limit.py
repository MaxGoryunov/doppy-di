"""Rate limiter devkit layer.

Examples:
    >>> from doppy_di import ContainerBuilder
    >>> from doppy_di.devkit import RateLimitedContainer
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> base = builder.build()
    >>> wrapped = RateLimitedContainer(base, max_resolutions_per_second=100)
    >>> wrapped.get("x")
    1
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from ..container import Container, Key, OverrideContext, Scope

logger = logging.getLogger("doppy_di.devkit.rate_limit")

RateLimitCallback = Callable[[Key, int, float], None]


def _unwrap_one(container: Any) -> Any | None:
    """Return inner wrapped container, or None if not a devkit wrapper."""
    if getattr(type(container), "_devkit", False) is not True:
        return None
    try:
        return object.__getattribute__(container, "wrapped")
    except AttributeError:
        return None


class ResolutionRateLimitExceededError(Exception):
    """Raised when resolution rate exceeds the configured limit."""

    def __init__(self, key: Key, count: int, limit: int, window: float) -> None:
        self.key = key
        self.count = count
        self.limit = limit
        self.window = window
        super().__init__(f"Rate limit exceeded for {key!r}: {count} in {window}s (limit {limit})")


class RateLimitedContainer:
    """Opt-in wrapper detecting excessive dev-mode resolution."""

    __slots__ = (
        "_clock",
        "_cumulative",
        "_lock",
        "_per_key",
        "_total",
        "count_cache_hits",
        "count_lifetimes",
        "ignore_keys",
        "max_count",
        "on_limit",
        "on_limit_callback",
        "report_mode",
        "window_seconds",
        "wrapped",
    )

    _devkit = True

    def __init__(
        self,
        wrapped: Any,
        max_resolutions_per_second: float = 100.0,
        window_seconds: float = 1.0,
        on_limit: str = "warn",
        on_limit_callback: Optional[RateLimitCallback] = None,
        ignore_keys: Optional[Set[Key]] = None,
        count_cache_hits: bool = False,
        count_lifetimes: Optional[Set[str]] = None,
        report_mode: str = "window",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if on_limit not in ("warn", "raise", "callback"):
            raise ValueError(f"Unknown on_limit: {on_limit!r}")
        if on_limit == "callback" and on_limit_callback is None:
            raise ValueError("on_limit_callback required")
        if report_mode not in ("window", "cumulative"):
            raise ValueError(f"Unknown report_mode: {report_mode!r}")
        self.wrapped = wrapped
        self.window_seconds = window_seconds
        self.max_count = max(1, int(max_resolutions_per_second * window_seconds))
        self.on_limit = on_limit
        self.on_limit_callback = on_limit_callback
        self.ignore_keys: Set[str] = {repr(k) for k in (ignore_keys or set())}
        self.count_cache_hits = count_cache_hits
        self.count_lifetimes = count_lifetimes or {"transient", "scoped", "singleton"}
        self.report_mode = report_mode
        self._per_key: Dict[str, Deque[float]] = {}
        self._total: Deque[float] = deque()
        self._cumulative: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _check(self, lookup: Key, cache_hit: bool) -> None:
        if cache_hit and not self.count_cache_hits:
            return
        norm = repr(lookup)
        if norm in self.ignore_keys:
            return
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            while self._total and self._total[0] <= cutoff:
                self._total.popleft()
            self._total.append(now)
            dq = self._per_key.get(norm)
            if dq is None:
                dq = deque()
                self._per_key[norm] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            dq.append(now)
            count = len(dq)
            self._cumulative[norm] = self._cumulative.get(norm, 0) + 1
        if count > self.max_count:
            if self.on_limit == "warn":
                logger.warning("Rate limit exceeded for %r: %d", lookup, count)
            elif self.on_limit == "raise":
                raise ResolutionRateLimitExceededError(
                    lookup, count, self.max_count, self.window_seconds
                )
            elif self.on_limit_callback is not None:
                self.on_limit_callback(lookup, count, self.window_seconds)

    def _is_cache_hit(self, lookup: Key) -> bool:
        inner: Any = self.wrapped
        seen = 0
        nxt = _unwrap_one(inner)
        while nxt is not None and seen < 32:
            inner = nxt
            nxt = _unwrap_one(inner)
            seen += 1
        if isinstance(inner, Container):
            try:
                return lookup in inner.single
            except TypeError:
                return False
        return False

    def get(self, key: Key) -> Any:
        """Resolve key with rate accounting."""
        hit = self._is_cache_hit(key)
        self._check(key, hit)
        return self.wrapped.get(key)

    async def aget(self, key: Key) -> Any:
        """Resolve key asynchronously with rate accounting."""
        hit = self._is_cache_hit(key)
        self._check(key, hit)
        fn = getattr(self.wrapped, "aget", None)
        res = fn(key) if fn is not None else self.wrapped.get(key)
        if asyncio.iscoroutine(res):
            return await res
        return res

    def has(self, key: Key) -> bool:
        """Check registration."""
        result: bool = self.wrapped.has(key)
        return result

    def scope(self, name: str) -> Scope:
        """Return scope from wrapped container."""
        result: Scope = self.wrapped.scope(name)
        return result

    def override(self, key: Key, value: Any) -> OverrideContext:
        """Override value in wrapped container."""
        result: OverrideContext = self.wrapped.override(key, value)
        return result

    def usage_report(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """Return top keys by resolution frequency."""
        with self._lock:
            if self.report_mode == "cumulative":
                items = sorted(self._cumulative.items(), key=lambda kv: kv[1], reverse=True)
                return items[:top_n]
            now = self._clock()
            cutoff = now - self.window_seconds
            counts: List[Tuple[str, int]] = []
            for norm, dq in self._per_key.items():
                counts.append((norm, sum(1 for t in dq if t > cutoff)))
            counts.sort(key=lambda kv: kv[1], reverse=True)
            return counts[:top_n]

    def wrapper_depth(self) -> int:
        """Return nesting depth of devkit wrappers."""
        depth = 1
        inner = _unwrap_one(self.wrapped)
        while inner is not None:
            depth += 1
            inner = _unwrap_one(inner)
        return depth


def unwrap(container: Any) -> Any:
    """Return innermost non-wrapper container."""
    seen = 0
    inner = _unwrap_one(container)
    while inner is not None and seen < 64:
        container = inner
        inner = _unwrap_one(container)
        seen += 1
    return container


def ensure_unwrapped(container: Any, mode: str = "warn") -> Any:
    """Warn or raise if container still wrapped for production."""
    if _unwrap_one(container) is None:
        return container
    msg = "Container still wrapped with devkit layer; unwrap before production"
    if mode == "raise":
        raise RuntimeError(msg)
    logger.warning(msg)
    return container
