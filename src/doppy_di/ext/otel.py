"""OpenTelemetry adapter for container tracing.

The adapter translates :class:`~doppy_di.container.Container` trace events
into OpenTelemetry spans. OpenTelemetry is loaded lazily, so importing this
module does not require ``opentelemetry-api`` to be installed unless
:func:`otel_adapter` is called.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..container import Key, TracerFn


class OpenTelemetryTracer:
    """Translate container trace events into OpenTelemetry spans.

    The object is itself callable and can be passed directly to
    :meth:`~doppy_di.container.Container.set_tracer`.

    Args:
        tracer: OpenTelemetry tracer instance (``opentelemetry.trace.Tracer``).
        span_name: Optional callable producing a span name from a key.
            Defaults to ``"doppy.resolve:{key!r}"``.

    Examples:
        >>> from doppy_di.ext.otel import OpenTelemetryTracer
        >>> class FakeSpan:
        ...     def __init__(self):
        ...         self.attrs = {}
        ...     def set_attribute(self, name, value):
        ...         self.attrs[name] = value
        ...     def end(self, **kwargs):
        ...         pass
        >>> class FakeTracer:
        ...     def __init__(self):
        ...         self.spans = []
        ...     def start_span(self, name, **kwargs):
        ...         span = FakeSpan()
        ...         self.spans.append((name, span))
        ...         return span
        >>> fake = FakeTracer()
        >>> adapter = OpenTelemetryTracer(fake)
        >>> adapter("a", 0.1, False, None)
        >>> fake.spans[0][0]
        "doppy.resolve:'a'"
        >>> fake.spans[0][1].attrs["doppy.cache_hit"]
        False
    """

    def __init__(
        self,
        tracer: Any,
        span_name: Optional[Callable[[Key], str]] = None,
    ) -> None:
        """Create an adapter around an OpenTelemetry tracer."""
        self._tracer = tracer
        self._span_name = span_name or (lambda key: f"doppy.resolve:{key!r}")

    def __call__(
        self,
        key: Key,
        duration: float,
        cache_hit: bool,
        scope: Optional[str],
    ) -> None:
        """Record one resolution as a span."""
        start_ns = time.time_ns()
        span = self._tracer.start_span(self._span_name(key), start_time=start_ns)
        span.set_attribute("doppy.key", repr(key))
        span.set_attribute("doppy.duration", duration)
        span.set_attribute("doppy.cache_hit", cache_hit)
        if scope is not None:
            span.set_attribute("doppy.scope", scope)
        span.end(end_time=time.time_ns())


def otel_adapter(
    tracer: Optional[Any] = None,
    span_name: Optional[Callable[[Key], str]] = None,
) -> TracerFn:
    """Return a :data:`TracerFn` emitting OpenTelemetry spans.

    When ``tracer`` is omitted the global ``"doppy-di"`` OpenTelemetry tracer
    is used. Requires ``opentelemetry-api`` to be installed; it is loaded
    lazily so the rest of the library still works without it.

    Examples:
        >>> adapter = otel_adapter()
        >>> isinstance(adapter, OpenTelemetryTracer)
        True
    """
    if tracer is None:
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]

        tracer = otel_trace.get_tracer("doppy-di")
    return OpenTelemetryTracer(tracer, span_name)
