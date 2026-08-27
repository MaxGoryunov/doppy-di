[![CI](https://github.com/MaxGoryunov/doppy-di/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxGoryunov/doppy-di/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/MaxGoryunov/doppy-di/branch/main/graph/badge.svg)](https://codecov.io/gh/MaxGoryunov/doppy-di)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/doppy-di)](https://pypi.org/project/doppy-di/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

[![Downloads](https://img.shields.io/pypi/dm/doppy-di)](https://pypi.org/project/doppy-di/)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://maxgoryunov.github.io/doppy-di/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](https://pypi.org/project/doppy-di/)
[![PEP 561](https://img.shields.io/badge/PEP%20561-typed-brightgreen)](https://peps.python.org/pep-0561/)
[![async ready](https://img.shields.io/badge/async-ready-brightgreen)](https://docs.python.org/3/library/asyncio.html)

[![Maintainability](https://qlty.sh/gh/MaxGoryunov/projects/doppy-di/maintainability.svg)](https://qlty.sh/gh/MaxGoryunov/projects/doppy-di)
[![CodeFactor](https://www.codefactor.io/repository/github/maxgoryunov/doppy-di/badge)](https://www.codefactor.io/repository/github/maxgoryunov/doppy-di)
[![Scrutinizer Code Quality](https://scrutinizer-ci.com/g/MaxGoryunov/doppy-di/badges/quality-score.png?b=main)](https://scrutinizer-ci.com/g/MaxGoryunov/doppy-di/?branch=main)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=bugs)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)
[![Duplicated Lines (%)](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=duplicated_lines_density)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)
[![Technical Debt](https://sonarcloud.io/api/project_badges/measure?project=MaxGoryunov_doppy-di&metric=sqale_index)](https://sonarcloud.io/summary/new_code?id=MaxGoryunov_doppy-di)

[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](https://github.com/python/mypy)
[![linting: ruff](https://img.shields.io/badge/linting-ruff-302D41)](https://github.com/astral-sh/ruff)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Hatch](https://img.shields.io/badge/%F0%9F%A5%9A-Hatch-4051b5.svg)](https://github.com/pypa/hatch)


[![Hits-of-Code](https://hitsofcode.com/github/MaxGoryunov/doppy-di?branch=main&exclude=.gitignore,uv.lock)](https://hitsofcode.com/github/MaxGoryunov/doppy-di/view?branch=main&exclude=.gitignore,uv.lock)
[![LoC](https://MaxGoryunov.github.io/doppy-di/badge.svg)](https://github.com/MaxGoryunov/doppy-di)
[![GitHub issues](https://img.shields.io/github/issues/MaxGoryunov/doppy-di)](https://github.com/MaxGoryunov/doppy-di/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/MaxGoryunov/doppy-di)](https://github.com/MaxGoryunov/doppy-di/pulls)

Minimal dependency injection container for Python. Provides immutable rule definitions, singleton/transient lifetimes, scoped caching, nested attribute resolution, cycle detection, and optional validation, logging, and ordering layers. Includes auto-wiring, function injection, yield-provider finalization, qualifiers, graph validation, dependency visualization, framework integrations, parallel async resolution, and modern typing support.

## How to use it

### Installation

```bash
pip install doppy-di
```

Requires Python 3.10 or later.

### Basic usage

```python
from doppy_di.container import ContainerBuilder

builder = ContainerBuilder()

# Register a singleton service
builder.service("answer", lambda: 42, lifetime="singleton")

# Register a transient service with dependencies
builder.service("greeting", lambda name: f"Hello, {name}!", deps=["name"])
builder.value("name", "World")

container = builder.build()

print(container.get("answer"))    # 42
print(container.get("greeting"))  # Hello, World!
```

### Scoped caching

```python
with container.scope("request") as scope:
    a = scope.get("greeting")
    b = scope.get("greeting")
    assert a is b  # cached within scope
# scope cache is cleared on exit
```

### Override for testing

```python
with container.override("answer", 99):
    print(container.get("answer"))  # 99
print(container.get("answer"))      # restored to 42
```

Override on an unregistered key raises `UnregisteredTypeError` — prevents silent no-op overrides.

## Use cases

### Service registration with dependency injection

Register factories with explicit lifetime and dependency list. Container resolves the dependency graph on first access.

```python
builder.service("db", lambda: Database("sqlite:///app.db"), lifetime="singleton")
builder.service("repo", lambda db: Repository(db), deps=["db"])
container = builder.build()
repo = container.get("repo")
```

### Value objects and constants

Inject pre-computed values or configuration objects.

```python
builder.value("config", {"debug": True, "port": 8080})
container.get("config")  # {"debug": True, "port": 8080}
```

### Aliasing

Create an alias that delegates resolution to another key.

```python
builder.service("real_service", lambda: Service(), lifetime="singleton")
builder.alias("service", "real_service")
assert container.get("service") is container.get("real_service")
```

### Nested attribute resolution

Access nested attributes of resolved services using tuple keys.

```python
builder.service("db", lambda: Database("prod"), lifetime="singleton")
# resolve db.connection directly
container.get(("db", "connection"))  # returns db.connection
```

### Scoped request context

Use named scopes for per-request caching without polluting the global singleton cache.

```python
def handle_request(request_id: str) -> dict:
    with container.scope(request_id) as scope:
        user = scope.get("current_user")
        data = scope.get("request_data")
        return process(user, data)
```

### Validation at build time

Enable build-time validation to catch missing dependencies early.

```python
builder.service("a", lambda b: A(b), deps=["b"])
try:
    container = builder.build(validate=True)
except ContainerBuildError as e:
    print(e.missing)  # [("a", "b")]
```

### Duplicate key policy

Control behaviour on duplicate registration.

```python
from doppy_di.container import DuplicateKeyPolicy

strict = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.FAIL)
strict.service("x", lambda: 1)
strict.service("x", lambda: 2)  # raises DuplicateKeyError

warning = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.WARN)
warning.service("x", lambda: 1)
warning.service("x", lambda: 2)  # logs warning, overwrites
```

### Optional runtime layers

The `devkit` package provides optional extensions:

```python
from doppy_di.devkit import LoggingContainer, ValidatingContainer

container = LoggingContainer(container)           # log all get operations
container = ValidatingContainer(container)         # validate before resolving
```

```python
from doppy_di.devkit.nested import NestedRules, SameValuePolicy

nested = NestedRules()
nested.add_rule("parent", "child", SameValuePolicy())
```

```python
from doppy_di.devkit import ChildrenFirstPolicy, ParentFirstPolicy
from doppy_di.devkit.policy import OrderPolicy

# control the order of nested field resolution
policy = ChildrenFirstPolicy()
```

### Auto-wiring

Mark classes with `@injectable` for automatic registration. `Container.scan()` discovers all injectable classes in a package; lazy registration on `get()` works without `scan()`.

```python
from doppy_di import injectable
from doppy_di.container import ContainerBuilder

@injectable(scope="singleton")
class Database:
    pass

@injectable
class Service:
    def __init__(self, repo: Database) -> None:
        self.repo = repo

builder = ContainerBuilder()
container = builder.build()
container.scan(__name__)          # batch discovery
svc = container.get(Service)      # or lazy: no scan() needed
```

### Function injection

Use `@inject` and `Depends()` to inject dependencies into plain functions and methods. Supports sync and async.

```python
from doppy_di import inject, Depends

@inject(container=container)
def handle_event(event: Event, service: UserService = Depends()):
    return service.process(event)
```

### Yield providers

Register generator factories for resources that need cleanup. The scope calls `close()` on exit.

```python
def make_session():
    try:
        yield Database()
    finally:
        cleanup()

builder.service("session", make_session, lifetime="transient")
with container.scope("req") as scope:
    session = scope.get("session")   # acquires
# session finalized on scope exit
```

Async generators are supported via `async with container.ascope()`.

### Qualifiers

Register multiple rules for the same type using a `qualifier` string.

```python
builder.service(Database, qualifier="read", factory=lambda: Database("read"))
builder.service(Database, qualifier="write", factory=lambda: Database("write"))

read_db = container.get(Database, qualifier="read")
```

### Graph validation

Call `container.validate()` to check the entire dependency graph at once, without resolving.

```python
errors = container.validate(strict=False)   # collect all errors
container.validate(strict=True)             # raise on first error
```

### Graph visualization

Render the dependency graph as Mermaid, Graphviz, or JSON.

```python
print(container.visualize("mermaid"))   # graph TD  Service --> Database
print(container.visualize("graphviz"))  # digraph G { Service -> Database; }
data = container.visualize("json")      # {"Service": {"deps": ["Database"]}}
```

### Parallel async resolution

Resolve independent dependencies concurrently with `get_many()`.

```python
a, b = await container.get_many(["a", "b"], parallel=True)
```

Async containers also support `aget()` and `ascope()`.

### Async-first resolution

`aget()` resolves sync and async factories, sync and async resources, and
resolves independent dependency branches concurrently. Sync factories are
called directly with no `await` overhead.

```python
async def make_db():
    return Database("async")

builder.service("db", make_db)
container = builder.build()

db = await container.aget("db")
```

Async yield providers are finalized on cancellation:

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

Mixed-graph rules: a sync factory depending on an async dependency raises
`AsyncDependencyInSyncContextError` when resolved via `get()`. A sync factory
returning an awaitable raises `SyncFactoryReturningAwaitableError`. Cancelled
`aget()` finalizes partially-created resources and raises
`ResolutionCancelledError`.

### Provider facade

Declarative providers convert to rules on assignment. Import from
`doppy_di.providers`; the package-level `Factory` protocol is untouched.

```python
from doppy_di import Container, Scope
from doppy_di.providers import Factory, Singleton, Value, Resource

services = Container()
services.config = Value({"debug": True})
services.db = Resource(create_db, Scope.APP)
services.repo = Factory(UserRepository, db=services.db)
services.service = Singleton(UserService, repo=services.repo)
```

Providers: `Factory`, `Singleton`, `Scoped`, `Value`, `Resource`,
`Coroutine`, `Alias`, `Selector`, `ListOf`, `DictOf`. Assignment is
attribute-style; dependencies may reference other providers before they are
assigned.

### Config profiles and child containers

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
visible. `diff(other)` returns a `DiffReport` of added/removed/changed keys.
`export_config()` serializes the effective configuration to JSON.

### Compile / plan mode

Compile the graph once into an immutable `ExecutionPlan`.

```python
plan = container.compile()
assert plan.get("b") == 2
```

`compile()` validates the full graph up front. The plan is immutable and
resolves through the live container, so lifetimes, caches and scopes keep
identical semantics. `ExecutionPlan.serialize()` / `deserialize()` persist
the plan to JSON. Fully opt-in: if `compile()` is never called there is zero
overhead.

## Speed

Resolution stays pure Python with no `compile`-based code generation via
`exec` and no compiled extension. The compiled `ExecutionPlan` pre-builds
resolver closures, so a resolve is a plain dict lookup plus a call. On the
six-object register-user graph, the fastest path (`compile(guardless=True)`)
resolves at sub-microsecond medians with two singletons and the full feature
set enabled — hand-written code only a hair faster, and pacing `exec`-based
DI containers. Full numbers on the [speed comparison](https://MaxGoryunov.github.io/doppy-di/) page.

### Observability and tracing

Set a tracer callback to observe every resolution.

```python
events = []

def tracer(key, duration, cache_hit, scope):
    events.append((key, duration, cache_hit, scope))

container.set_tracer(tracer)
container.get("a")
container.get("a")  # cache hit
```

Pass `set_tracer(None)` to disable. When no tracer is set there is no timing
and no dispatch — zero overhead. Child containers inherit the parent tracer.

OpenTelemetry integration via the optional extra:

```bash
pip install "doppy-di[otel]"
```

```python
from doppy_di.ext.otel import otel_adapter

container.set_tracer(otel_adapter())
container.get("a")  # emits doppy.resolve:'a' span
```

### Pluggable resolution policies

Control the order of dependency resolution. Policies are opt-in; the default
behaviour is unchanged when none is specified.

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

Note: resolution policies are exported under the aliases
`ResolutionChildrenFirstPolicy` / `ResolutionParentFirstPolicy`. The
top-level `ChildrenFirstPolicy` / `ParentFirstPolicy` names belong to the
devkit nested-field ordering (see "Optional runtime layers" above).

### Graph introspection and CLI

Query the dependency graph programmatically.

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

`check` reports missing dependencies, cycles, duplicate registrations,
unused registrations (with `--root`), and lifetime violations.

### Framework integrations

Optional first-party integrations for FastAPI, aiogram, and Typer live in `doppy_di.ext.*`.

```python
from doppy_di.ext.fastapi import setup_doppy
setup_doppy(app, container)                 # per-request scope

from doppy_di.ext.aiogram import setup_doppy
setup_doppy(bot, container)                 # per-update scope

from doppy_di.ext.typer import setup_doppy
setup_doppy(app, container)                 # inject into commands
```

### Modern typing support

The public API supports `TypeAlias`, `TypedDict`, `ParamSpec`, `TypeGuard`, and `Self` for improved static checking with mypy strict. No runtime overhead.

Additional information can be found in [Documentation](https://maxgoryunov.github.io/doppy-di/).

## How to contribute

1. Fork the repository.
2. Create a feature branch (`git checkout -b feat/my-feature`).
3. Install development dependencies: `uv sync --extra dev`.
4. Make changes. Format and lint with: `uv run ruff format . && uv run ruff check --fix .`
5. Type-check: `uv run mypy`.
6. Run tests: `uv run pytest`.
7. Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/) (enforced via commitlint).
8. Open a pull request against `main`.