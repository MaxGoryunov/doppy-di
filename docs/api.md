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
```

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

## Provider facade

Declarative providers convert to container rules on attribute assignment.
They are inert data objects: no resolution logic, zero overhead until
assigned. Import from `doppy_di.providers`; the package-level `Factory`
protocol is untouched.

```python
from doppy_di import Container, Scope
from doppy_di.providers import Factory, Singleton, Value, Resource

services = Container()
services.config = Value({"debug": True})
services.db = Resource(create_db, Scope.APP)
services.repo = Factory(UserRepository, db=services.db)
services.service = Singleton(UserService, repo=services.repo)
```

Assigning a class factory registers both the named key and a type key.
Dependencies may reference providers before assignment; unbound placeholders
resolve by name at rule registration.

### Provider classes

- `Factory` — transient factory.
- `Singleton` — singleton factory.
- `Scoped` — factory cached per scope.
- `Value` — constant value.
- `Resource` — yield-based resource finalized on scope exit.
- `Coroutine` — async factory.
- `Alias` — points at another key.
- `Selector` — picks one provider at resolution time.
- `ListOf` — aggregates providers into a list.
- `DictOf` — aggregates named providers into a dict.

## Async resolution

`Container.aget()` resolves sync and async factories, sync and async
resources, and resolves independent dependency branches concurrently. Sync
factories are called directly with no `await` overhead. `Container.ascope()`
provides an async scope; async yield providers are finalized on scope exit
and on cancellation.

```python
async def make_db():
    return Database("async")

builder.service("db", make_db)
container = builder.build()
db = await container.aget("db")

async with container.ascope("req") as scope:
    session = await scope.aget("session")
# async resources finalized on scope exit
```

`Container.get_many(keys, parallel=True)` resolves independent keys
concurrently.

### Async errors

- `AsyncDependencyInSyncContextError` — async dependency resolved via sync
  `get()`.
- `SyncFactoryReturningAwaitableError` — sync factory returned an awaitable.
- `ResolutionCancelledError` — `aget()` cancelled after partially creating
  resources; partially-created resources are finalized automatically.

## Resolution policies

Pluggable policies control the order of dependency resolution. Policies are
opt-in; default behaviour is unchanged when none is specified.

```python
from doppy_di import (
    ResolutionChildrenFirstPolicy,
    DefaultResolutionPolicy,
    EagerPolicy,
    ParallelPolicy,
)

# container-wide policy
container = builder.build(policy=ResolutionChildrenFirstPolicy())

# per-call policy
container.get("a", policy=EagerPolicy())
```

### Built-in policies

- `DefaultResolutionPolicy` — resolve only the requested key (historical
  behaviour).
- `LazyPolicy` — same as default; nothing resolved until `get()`.
- `ParentFirstPolicy` — parents before children.
- `ChildrenFirstPolicy` — children before parents.
- `EagerPolicy` — resolve the entire graph up front.
- `ParallelPolicy` — resolve dependency levels concurrently in `aget()`
  (sequential in sync `get()`).

Implement the `ResolutionPolicy` protocol (`order(graph, root)`) for custom
strategies.

### Name disambiguation

The top-level `ChildrenFirstPolicy`/`ParentFirstPolicy` names belong to the
devkit nested-field ordering. Resolution policies are exported under the
aliases `ResolutionChildrenFirstPolicy`/`ResolutionParentFirstPolicy`.
Import resolution policies from `doppy_di` using the aliases, or directly
from `doppy_di.resolution`.

## Graph introspection

`Container.graph()` returns a `DependencyGraph` for programmatic querying.

```python
g = container.graph()
g.nodes()               # all registered keys
g.edges()               # (key, dependency) pairs
g.dependencies_of("a")  # direct deps
g.dependents_of("a")    # direct dependents
g.to_mermaid()          # mermaid
g.to_dot()              # graphviz
g.to_json()             # dict
g.to_text()             # text tree
```

`DependencyGraph` is exported from the package root and
`doppy_di.graph`.

## Profiles and child containers

Derive environment-specific containers without mutating the base.

```python
builder.value("env", "base")
container = builder.build()
prod = container.with_profile("prod", {"env": "prod"})
```

- `with_profile(name, overrides)` — layered container with profile
  overrides.
- `child(name=None)` — container layered over the parent; parent rules added
  later stay visible.
- `diff(other)` — returns `DiffReport` of `added`/`removed`/`changed`
  keys.
- `export_config(format="json")` — serialize the effective configuration to
  JSON.

## Tracing

`Container.set_tracer()` registers a callback invoked after each successful
resolution.

```python
def tracer(key, duration, cache_hit, scope):
    ...

container.set_tracer(tracer)
container.set_tracer(None)  # disable
```

The callback receives `(key, duration, cache_hit, scope)`. With no tracer
set there is no timing and no dispatch — zero overhead. Child containers
inherit the parent tracer.

### OpenTelemetry

```bash
pip install "doppy-di[otel]"
```

```python
from doppy_di.ext.otel import otel_adapter

container.set_tracer(otel_adapter())
container.get("a")  # emits doppy.resolve:'a' span
```

`TracerFn` is the callback protocol:
`Callable[[Key, float, bool, Optional[str]], None]`.