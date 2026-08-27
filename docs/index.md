# doppy-di

Minimal dependency injection container for Python.

## Features

- **Builder pattern** — `ContainerBuilder` with `.service()`, `.value()`, `.alias()`.
- **Lifetime control** — `singleton` and `transient` lifetimes.
- **Scoped caching** — `with container.scope("req") as s:` for request-scoped objects.
- **Cycle detection** — automatic on every rule addition.
- **Duplicate-key policies** — `OVERWRITE`, `FAIL`, `WARN`.
- **Temporary overrides** — `with container.override("key", value):`.
- **Build validation** — `builder.build(validate=True)` catches missing dependencies.
- **Thread safety** — double-checked locking on singleton resolution.
- **Devkit extensions** — `ValidatingContainer`, `LoggingContainer`, `NestedRules`, order policies.
- **Async-first resolution** — `aget()`, `ascope()`, parallel branch resolution, cancellation-safe resource cleanup.
- **Provider facade** — declarative `Factory`, `Singleton`, `Scoped`, `Value`, `Resource`, `Coroutine`, `Alias`, `Selector`, `ListOf`, `DictOf` providers.
- **Profiles and child containers** — `with_profile()`, `child()`, `diff()`, `export_config()`.
- **Compile / plan mode** — immutable `ExecutionPlan` with JSON serialization.
- **Observability** — `set_tracer()` callbacks and OpenTelemetry integration.
- **Pluggable resolution policies** — `DefaultResolutionPolicy`, `EagerPolicy`, `ParallelPolicy`, and more.
- **Graph introspection and CLI** — `container.graph()`, `doppy-di graph/explain/check`.
- **Auto-wiring** — `@injectable`, `Container.scan()`, lazy type-based resolution.
- **Function injection** — `@inject` with `Depends()`.
- **Yield providers** — generator-based resources finalized on scope exit.
- **Qualifiers** — named dependencies via `Annotated`.
- **Framework integrations** — FastAPI, aiogram, Typer.
- **Modern typing support** — `TypeAlias`, `TypedDict`, `ParamSpec`, `TypeGuard`, `Self`.

## Quick start

```python
from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.service("answer", lambda: 42)
container = builder.build()

assert container.get("answer") == 42
```

## Next steps

- [Installation](installation.md) — pip / uv setup.
- [Usage](usage.md) — full design with examples.
- [Speed comparison](speed.md) — benchmarks vs other DI containers.
- [API](api.md) — reference documentation.