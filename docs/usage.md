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

Temporarily replace a value for the duration of a `with` block.

```python
builder = ContainerBuilder()
builder.value("x", 1)
c = builder.build()

with c.override("x", 99):
    assert c.get("x") == 99

assert c.get("x") == 1
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