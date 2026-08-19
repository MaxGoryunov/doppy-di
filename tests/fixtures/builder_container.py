"""Container fixture exposing only a builder."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.value("db", "postgres://localhost")
