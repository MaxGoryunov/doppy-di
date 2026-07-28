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
- [Usage](usage.md) — full guide with examples.
- [API](api.md) — reference documentation.