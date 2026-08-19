"""Container fixture with a missing dependency for CLI check tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.value("db", "postgres://localhost")
builder.service("repo", lambda db: db, deps=["db"])
builder.service("service", lambda missing: missing, deps=["missing"])
container = builder.build()
