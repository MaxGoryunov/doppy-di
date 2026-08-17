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