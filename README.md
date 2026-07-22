[![CI](https://github.com/MaxGoryunov/doppy-di/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxGoryunov/doppy-di/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/MaxGoryunov/doppy-di/branch/main/graph/badge.svg)](https://codecov.io/gh/MaxGoryunov/doppy-di)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)


[![PyPI version](https://img.shields.io/pypi/v/doppy-di)](https://pypi.org/project/doppy-di/)
[![PyPI status](https://img.shields.io/pypi/status/doppy-di)](https://pypi.org/project/doppy-di/)
[![Downloads](https://img.shields.io/pypi/dm/doppy-di)](https://pypi.org/project/doppy-di/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](https://pypi.org/project/doppy-di/)

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

[![SemVer](https://img.shields.io/badge/dynamic/json?url=https://pypi.org/pypi/doppy-di/json&query=%24.info.version&label=SemVer&color=brightgreen)](https://semver.org/)<!-- [![typed](https://img.shields.io/badge/typed-Yes-brightgreen)](https://peps.python.org/pep-0561/) -->
[![PEP 561](https://img.shields.io/badge/PEP%20561-typed-brightgreen)](https://peps.python.org/pep-0561/)
[![async ready](https://img.shields.io/badge/async-ready-brightgreen)](https://docs.python.org/3/library/asyncio.html)

[![Hits-of-Code](https://hitsofcode.com/github/MaxGoryunov/doppy-di?branch=main&exclude=.gitignore,uv.lock)](https://hitsofcode.com/github/MaxGoryunov/doppy-di/view?branch=main&exclude=.gitignore,uv.lock)
[![SLOC](https://tokei.rs/b1/github/MaxGoryunov/doppy-di?category=code)](https://github.com/MaxGoryunov/doppy-di)
[![GitHub issues](https://img.shields.io/github/issues/MaxGoryunov/doppy-di)](https://github.com/MaxGoryunov/doppy-di/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/MaxGoryunov/doppy-di)](https://github.com/MaxGoryunov/doppy-di/pulls)

Minimal dependency injection container for Python. Provides immutable rule definitions, singleton/transient lifetimes, scoped caching, nested attribute resolution, cycle detection, and optional validation, logging, and ordering layers.

## How to use it

### Installation

```bash
pip install doppy-di
```

Requires Python 3.9 or later.

### Basic usage

```python
from container import ContainerBuilder

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
from container import DuplicateKeyPolicy

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
from devkit import LoggingContainer, ValidatingContainer

container = LoggingContainer(container)           # log all get operations
container = ValidatingContainer(container)         # validate before resolving
```

```python
from devkit.nested import NestedRules, SameValuePolicy

nested = NestedRules()
nested.add_rule("parent", "child", SameValuePolicy())
```

```python
from devkit import ChildrenFirstPolicy, ParentFirstPolicy
from devkit.policy import OrderPolicy

# control the order of nested field resolution
policy = ChildrenFirstPolicy()
```

## How to contribute

1. Fork the repository.
2. Create a feature branch (`git checkout -b feat/my-feature`).
3. Install development dependencies: `uv sync --extra dev`.
4. Make changes. Format and lint with: `uv run ruff format . && uv run ruff check --fix .`
5. Type-check: `uv run mypy`.
6. Run tests: `uv run pytest`.
7. Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/) (enforced via commitlint).
8. Open a pull request against `main`.