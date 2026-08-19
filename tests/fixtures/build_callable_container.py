"""Container fixture exposing a build() callable."""

from doppy_di import Container, ContainerBuilder


def build() -> Container:
    builder = ContainerBuilder()
    builder.value("db", "postgres://localhost")
    return builder.build()
