"""Container fixture with a duplicate key for CLI check tests."""

from doppy_di import ContainerBuilder, DuplicateKeyPolicy

builder = ContainerBuilder(duplicate_policy=DuplicateKeyPolicy.FAIL)
builder.value("db", "postgres://localhost")
builder.value("db", "postgres://other")
container = builder.build()
