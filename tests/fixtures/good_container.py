"""Valid container fixture for CLI tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.value("db", "postgres://localhost")
builder.service("repo", lambda db: db, deps=["db"])
builder.service("service", lambda repo: repo, deps=["repo"])
container = builder.build()
