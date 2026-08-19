"""Container fixture with a lifetime violation for CLI check tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.service(
    "scoped",
    lambda: object(),
    lifetime="singleton",
    scope="request",
)
container = builder.build()
