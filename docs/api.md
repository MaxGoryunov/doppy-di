# API

::: doppy_di

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