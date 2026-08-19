"""Container fixture with no named attrs, only a Container instance."""

from doppy_di import ContainerBuilder

_builder = ContainerBuilder()
_builder.value("db", "postgres://localhost")
_container = _builder.build()
