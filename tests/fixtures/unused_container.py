"""Container fixture with an unused registration for CLI check tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.value("db", "postgres://localhost")
builder.service("repo", lambda db: db, deps=["db"])
builder.service("service", lambda repo: repo, deps=["repo"])
builder.value("orphan", 42)
container = builder.build()
