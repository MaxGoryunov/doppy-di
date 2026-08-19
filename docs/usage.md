# Usage

## 1. Basic container

```python
from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.service("greet", lambda: "Hello")
builder.value("pi", 3.14)

container = builder.build()
assert container.get("greet") == "Hello"
assert container.get("pi") == 3.14
```

## 2. Singleton vs transient

Singleton: same object on every `get`.  
Transient: new object on every `get`.

```python
builder = ContainerBuilder()
builder.service("s", lambda: object(), lifetime="singleton")
builder.service("t", lambda: object(), lifetime="transient")

c = builder.build()
assert c.get("s") is c.get("s")       # same
assert c.get("t") is not c.get("t")   # different
```

## 3. Dependencies

Factories receive resolved dependencies as positional args.

```python
builder = ContainerBuilder()
builder.value("greeting", "Hello")
builder.value("name", "World")
builder.service(
    "message",
    lambda greeting, name: f"{greeting}, {name}!",
    deps=["greeting", "name"],
)
c = builder.build()
assert c.get("message") == "Hello, World!"
```

## 4. Scopes

Scope caches resolved values within a `with` block. On exit the cache is cleared.

```python
builder = ContainerBuilder()
builder.service("x", lambda: object(), lifetime="transient")
c = builder.build()

with c.scope("req") as s:
    a = s.get("x")
    b = s.get("x")
    assert a is b   # cached within scope
```

Scopes can nest:

```python
with c.scope("outer") as outer:
    with c.scope("inner") as inner:
        ...
```

## 5. Aliases

An alias points to another key. Resolving an alias resolves the target.

```python
builder = ContainerBuilder()
builder.value("pi", 3.14)
builder.alias("π", "pi")

c = builder.build()
assert c.get("π") == 3.14
```

## 6. Overrides

Temporarily replace one or more values for the duration of a `with` block.
Overrides stack: nested `override()` blocks are LIFO, the last one wins, and
exiting restores the original rules — even on exception.

```python
builder = ContainerBuilder()
builder.value("x", 1)
builder.value("y", 2)
c = builder.build()

# single key
with c.override("x", 99):
    assert c.get("x") == 99

# dict of keys, nested stack
with c.override({"x": 10, "y": 20}):
    assert c.get("x") == 10
    assert c.get("y") == 20
    with c.override({"x": 30}):
        assert c.get("x") == 30   # last wins
    assert c.get("x") == 10

assert c.get("x") == 1
assert c.get("y") == 2
```

A callable override is treated as a factory and invoked on every resolution:

```python
with c.override({"x": lambda: 42}):
    assert c.get("x") == 42
```

Overrides are validated on entry. Overriding a singleton with a scoped
dependency or a resource with a plain value raises `ValueError`.

For pytest, wrap the container in a fixture:

```python
@pytest.fixture
def container_with_overrides(container):
    with container.override({"db": fake_db}):
        yield container
```

## 7. Duplicate-key policies

Control behaviour when the same key is registered twice.

```python
from doppy_di import DuplicateKeyPolicy

# FAIL — raise DuplicateKeyError
builder = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.FAIL)
builder.value("x", 1)
builder.value("x", 2)  # raises DuplicateKeyError

# WARN — log warning, overwrite
builder = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.WARN)

# OVERWRITE (default) — silently replace
builder = ContainerBuilder()
```

## 8. Build validation

Pass `validate=True` to catch missing dependencies at build time.

```python
builder = ContainerBuilder()
builder.service("a", lambda b: b.upper(), deps=["b"])

# raises ContainerBuildError: a -> b
c = builder.build(validate=True)
```

## 9. Cycle detection

Cycles are detected automatically when a rule is added.

```python
builder = ContainerBuilder()
builder.service("a", lambda b: b, deps=["b"])
builder.service("b", lambda a: a, deps=["a"])  # raises CycleError
```

## 10. Compile / plan mode

Compile the dependency graph once into an immutable `ExecutionPlan`:

```python
from doppy_di import CompilePolicy, ContainerBuilder, ExecutionPlan

builder = ContainerBuilder()
builder.value("a", 1)
builder.service("b", lambda a: a + 1, deps=["a"])
container = builder.build()

plan = container.compile()
assert plan.get("b") == 2
```

`compile()` validates the full graph up front: missing dependencies raise
`MissingDependencyError`, cycles raise `DependencyCycleError`. The plan is
immutable and resolves through the live container, so lifetimes, singleton
caches and scopes keep identical semantics.

### Override policy

Compiler is opt-in policy.

```python
# ALLOW_OVERRIDE (default): overrides still apply through the live container
builder = ContainerBuilder(compile_policy=CompilePolicy.ALLOW_OVERRIDE)
container = builder.build()
plan = container.compile()
with container.override("a", 10):
    assert plan.get("b") == 11

# STRICT: after compile() the container rejects further overrides
strict = ContainerBuilder(compile_policy=CompilePolicy.STRICT).build()
strict.compile()
strict.override("x", 1)  # raises RuntimeError
```

### Serialization

Plans persist graph topology, rule metadata and resolved singletons to JSON:

```python
data = plan.serialize()
restored = ExecutionPlan.deserialize(data)
assert restored.get("a") == 1
```

Factories are not serialized. After deserialization only registered singleton
values resolve; factory-backed keys raise `ServiceNotFoundError`. Use
module-level factory functions for true cross-process plan caching.

### Zero overhead

`compile()` is fully opt-in. If it is never called, no plan is built and no
extra work happens at resolution time.

## 11. Observability / tracing

Set a tracer callback to observe every resolution. The callback receives
`(key, duration, cache_hit, scope)` after each successful `get()`/`aget()`.

```python
events = []

def tracer(key, duration, cache_hit, scope):
    events.append((key, duration, cache_hit, scope))

builder = ContainerBuilder()
builder.value("a", 1)
container = builder.build()
container.set_tracer(tracer)

container.get("a")
container.get("a")  # cache hit

assert events[0] == ("a", _, False, None)  # miss
assert events[1] == ("a", _, True, None)   # hit
```

Pass `set_tracer(None)` to disable tracing. When no tracer is set there is
no timing and no dispatch — zero overhead. Child containers inherit the
parent tracer. Scope resolutions report the scope name as the last argument.

### OpenTelemetry

Install the optional extra and attach an adapter that emits spans:

```bash
pip install "doppy-di[otel]"
```

```python
from doppy_di import ContainerBuilder
from doppy_di.ext.otel import otel_adapter

builder = ContainerBuilder()
builder.value("a", 1)
container = builder.build()
container.set_tracer(otel_adapter())

container.get("a")  # emits doppy.resolve:'a' span
```

## 12. Async-first resolution

`aget()` resolves sync and async factories, sync and async resources, and
resolves independent dependency branches concurrently. Sync factories are
called directly with no `await` overhead.

```python
import asyncio

async def make_db():
    return "async-db"

builder = ContainerBuilder()
builder.service("db", make_db)
container = builder.build()

db = asyncio.run(container.aget("db"))
assert db == "async-db"
```

Async yield providers are finalized on scope exit and on cancellation:

```python
async def make_session():
    try:
        yield Database()
    finally:
        await cleanup()

builder.service("session", make_session)
container = builder.build()

async with container.ascope("req") as scope:
    session = await scope.aget("session")
# session finalized on scope exit
```

### Mixed-graph rules

A sync factory depending on an async dependency raises
`AsyncDependencyInSyncContextError` when resolved via `get()`. A sync factory
returning an awaitable raises `SyncFactoryReturningAwaitableError`. A
cancelled `aget()` finalizes partially-created resources and raises
`ResolutionCancelledError`.

### Parallel resolution

`get_many()` resolves independent keys concurrently:

```python
a, b = await container.get_many(["a", "b"], parallel=True)
```

## 13. Provider facade

Declarative providers convert to rules on attribute assignment. Import from
`doppy_di.providers`; the package-level `Factory` protocol is untouched.

```python
from doppy_di import Container, Scope
from doppy_di.providers import Factory, Singleton, Value, Resource

services = Container()
services.config = Value({"debug": True})
services.db = Resource(create_db, Scope.APP)
services.repo = Factory(UserRepository, db=services.db)
services.service = Singleton(UserService, repo=services.repo)

assert services.get("config") == {"debug": True}
```

Dependencies may reference providers before they are assigned; unbound
placeholders resolve by name at rule registration. Assigning a class factory
registers both the named key and a type key.

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

## 14. Config profiles and child containers

Derive environment-specific containers without mutating the base.

```python
builder = ContainerBuilder()
builder.value("env", "base")
container = builder.build()

prod = container.with_profile("prod", {"env": "prod"})
assert container.get("env") == "base"
assert prod.get("env") == "prod"
```

`child()` layers rules over the parent; parent rules added later stay
visible.

```python
child = container.child("worker")
child.value("role", "worker")
assert child.get("env") == "base"   # parent rule visible
```

`diff(other)` returns a `DiffReport` of added/removed/changed keys:

```python
report = container.diff(prod)
assert "env" in report.changed
```

`export_config()` serializes the effective configuration to JSON:

```python
config = container.export_config()
# '{"env": "base"}'
```

## 15. Resolution policies

Policies control the order of dependency resolution. Opt-in; default
behaviour unchanged when none specified.

```python
from doppy_di import (
    ResolutionChildrenFirstPolicy,
    EagerPolicy,
    ParallelPolicy,
)

# container-wide policy
container = builder.build(policy=ResolutionChildrenFirstPolicy())

# per-call policy
container.get("a", policy=EagerPolicy())
```

Built-in policies: `DefaultResolutionPolicy`, `LazyPolicy`,
`ResolutionParentFirstPolicy`, `ResolutionChildrenFirstPolicy`,
`EagerPolicy`, `ParallelPolicy`. Implement the `ResolutionPolicy` protocol
(`order(graph, root)`) for custom strategies.

Note: top-level `ChildrenFirstPolicy`/`ParentFirstPolicy` are devkit
nested-field ordering policies. Resolution policies use the
`ResolutionChildrenFirstPolicy`/`ResolutionParentFirstPolicy` aliases.

## 16. Graph introspection and CLI

Query the dependency graph programmatically:

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

Inspect or lint container definitions from a file:

```bash
doppy-di graph container.py --format mermaid
doppy-di explain db --file container.py
doppy-di check container.py --root service --strict
```

`graph` supports `mermaid`, `dot`, `json`, `text` formats. `explain` shows
lifetime, scope, dependencies and dependents of a key. `check` reports
missing dependencies, cycles, duplicate registrations, unused registrations
(with `--root`), and lifetime violations.

## Devkit extensions

### ValidatingContainer

Wrap a container with resolution ordering and validation rules.

```python
from doppy_di import (
    ContainerBuilder,
    UnorderedPolicy,
    ValidatingContainer,
    ValidationRunner,
)

builder = ContainerBuilder()
builder.value("x", 1)
base = builder.build()

wrapped = ValidatingContainer(
    base, UnorderedPolicy(), ValidationRunner()
)
assert wrapped.get("x") == 1
```

### LoggingContainer

Log every container operation.

```python
from doppy_di import ContainerBuilder, LoggingContainer

events = []

def log(msg: str) -> None:
    events.append(msg)

builder = ContainerBuilder()
builder.value("x", 1)
base = builder.build()

wrapped = LoggingContainer(base, log)
wrapped.get("x")

assert "get('x')" in events
```

### Nested rules

Validate that a resolved object's attribute matches a separately
resolved nested rule.

```python
from doppy_di import (
    ContainerBuilder,
    NestedRules,
    Rule,
    RuleSet,
)

class Service:
    def __init__(self):
        self.repo = "db"

nested = NestedRules()
rule = Rule(("service", "repo"), lambda: "db")
rs = RuleSet()
nested.add_nested("service", "repo", rule, rs)

builder = ContainerBuilder()
builder.service("service", lambda: Service())
base = builder.build()

# validate_nested checks attribute == resolved nested value
nested.validate_nested("service", base)
```
