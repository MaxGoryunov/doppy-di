# API

::: doppy_di

## Rich diagnostic errors

Errors carry resolution paths, scope names, and optional registration
sources.

### Error classes

- `MissingDependencyError` — deep dependency chain failure. Carries
  `key`, `resolution_path`, `scope`, `registration_source`. Subclasses
  `ServiceNotFoundError`.
- `DependencyCycleError` — dependency cycle. Carries `cycle`. Subclasses
  `CycleError`.
- `InvalidLifetimeError` — unknown lifetime. Carries `lifetime`.
  Subclasses `ValueError`.
- `ScopeViolationError` — scope/lifetime violation. Carries `key`,
  `scope`, `violation_type`.
- `FactoryExecutionError` — wraps a factory-body exception. Carries
  `key`, `original_exception`, `resolution_path`. Raised only when
  `wrap_factory_errors=True`.
- `DuplicateRegistrationError` — duplicate key under the FAIL policy.
  Carries `key`, `existing_source`, `new_source`. Subclasses `KeyError`.
- `ResourceFinalizationError` — yield-provider cleanup failure. Carries
  `errors: list[(key, exception)]`. Raised only when
  `finalization_errors=True`.

### Builder flags

```python
builder = ContainerBuilder(
    track_sources=True,          # capture filename/lineno on registration
    wrap_factory_errors=True,    # wrap factory exceptions
    finalization_errors=True,    # raise on yield-provider cleanup failure
    check_cycles_on_register=False,  # defer cycle detection to resolve
)
```

All flags default to `False` (or `True` for cycle checking), preserving
existing behavior.

## Modern typing support

The public API supports modern typing features for improved static checking
(mypy, pyright). All features are annotation-only — zero runtime overhead.

### `TypeAlias`

`TypeAlias` can be used as a service key:

```python
from typing import TypeAlias

DatabaseService: TypeAlias = Database

builder = ContainerBuilder()
builder.service(DatabaseService, make=lambda: Database())
container = builder.build()
db = container.get(DatabaseService)
```

### `TypedDict`

`TypedDict` classes are resolvable as dependencies:

```python
from typing import TypedDict

class DBConfig(TypedDict):
    host: str
    port: int

builder = ContainerBuilder()
builder.service(DBConfig, make=lambda: {"host": "localhost", "port": 5432})
container = builder.build()
config = container.get(DBConfig)
```

### `ParamSpec` factories

`Factory` protocol and `Provider` alias accept `ParamSpec`-typed callables:

```python
from typing import Callable, ParamSpec, TypeVar
from doppy_di import Factory, Provider

P = ParamSpec("P")
T = TypeVar("T")

def provider(factory: Callable[P, T]) -> Callable[P, T]:
    return factory

builder = ContainerBuilder()
builder.service(Database, make=provider(lambda: Database()))
```

### `TypeGuard` detection

`is_injectable()` narrows types at runtime:

```python
from doppy_di import injectable, is_injectable

@injectable
class Service:
    pass

if is_injectable(Service):
    # Service is narrowed to type here
    ...
```

### `Self` fluent builder

`ContainerBuilder.service()`, `value()`, and `alias()` return `Self` for
chaining:

```python
builder = ContainerBuilder()
builder.value("x", 1).service("y", lambda: 2).alias("z", "x")
container = builder.build()

## Compile / plan mode

`Container.compile()` returns an immutable `ExecutionPlan` that captures a
topological ordering of the registered rules. The plan validates the graph up
front (raising `MissingDependencyError` for unregistered dependencies and
`DependencyCycleError` for cycles), then resolves through the live container so
lifetimes, singleton caches and scopes keep identical semantics. The feature is
fully opt-in: if `compile()` is never called there is zero overhead.

```python
from doppy_di import CompilePolicy, ContainerBuilder

builder = ContainerBuilder(compile_policy=CompilePolicy.ALLOW_OVERRIDE)
builder.value("a", 1)
builder.service("b", lambda a: a + 1, deps=["a"])

container = builder.build()
plan = container.compile()
plan.get("b")  # 2

# ALLOW_OVERRIDE: overrides still apply through the live container
with container.override("a", 10):
    plan.get("b")  # 11

# STRICT: after compile() the container rejects further overrides
strict = ContainerBuilder(compile_policy=CompilePolicy.STRICT).build()
strict.compile()
# strict.override("a", 1)  # raises RuntimeError
```

`ExecutionPlan.serialize()` / `ExecutionPlan.deserialize()` persist the graph
topology, rule metadata and resolved singletons to JSON for caching or
cross-process reuse.
