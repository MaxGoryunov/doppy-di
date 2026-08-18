"""Tests for the OpenTelemetry adapter (issue 33)."""

import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple


class _FakeSpan:
    def __init__(self) -> None:
        self.attrs: Dict[str, Any] = {}

    def set_attribute(self, name: str, value: Any) -> None:
        self.attrs[name] = value

    def end(self, **kwargs: Any) -> None:
        self.ended = kwargs


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: List[Tuple[str, _FakeSpan]] = []

    def start_span(self, name: str, **kwargs: Any) -> _FakeSpan:
        span = _FakeSpan()
        self.spans.append((name, span))
        return span


def test_otel_adapter_emits_spans() -> None:
    from doppy_di.ext.otel import OpenTelemetryTracer

    fake = _FakeTracer()
    adapter = OpenTelemetryTracer(fake)

    adapter("a", 0.1, True, "req")

    assert fake.spans[0][0] == "doppy.resolve:'a'"
    span = fake.spans[0][1]
    assert span.attrs["doppy.key"] == "'a'"
    assert span.attrs["doppy.duration"] == 0.1
    assert span.attrs["doppy.cache_hit"] is True
    assert span.attrs["doppy.scope"] == "req"


def test_otel_adapter_custom_span_name_and_no_scope() -> None:
    from doppy_di.ext.otel import OpenTelemetryTracer

    fake = _FakeTracer()
    adapter = OpenTelemetryTracer(fake, span_name=lambda key: f"res:{key}")

    adapter("a", 0.5, False, None)

    assert fake.spans[0][0] == "res:a"
    span = fake.spans[0][1]
    assert "doppy.scope" not in span.attrs


def test_otel_adapter_from_container() -> None:
    from doppy_di.container import ContainerBuilder
    from doppy_di.ext.otel import OpenTelemetryTracer

    fake = _FakeTracer()
    adapter = OpenTelemetryTracer(fake)
    builder = ContainerBuilder()
    builder.value("a", 1)
    container = builder.build()
    container.set_tracer(adapter)

    container.get("a")

    assert fake.spans[0][1].attrs["doppy.cache_hit"] is False


def test_otel_adapter_default_tracer(monkeypatch: Any) -> None:
    from doppy_di.ext.otel import OpenTelemetryTracer, otel_adapter

    fake = _FakeTracer()
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry",
        SimpleNamespace(trace=SimpleNamespace(get_tracer=lambda _name: fake)),
    )

    adapter = otel_adapter()

    assert isinstance(adapter, OpenTelemetryTracer)
    assert adapter._tracer is fake
