"""Container fixture with a dependency cycle for CLI check tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder(check_cycles_on_register=False)
builder.service("a", lambda b: b, deps=["b"])
builder.service("b", lambda a: a, deps=["a"])
container = builder.build()
