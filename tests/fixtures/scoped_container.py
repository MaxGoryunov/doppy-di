"""Container fixture with a scoped rule for explain tests."""

from doppy_di import ContainerBuilder

builder = ContainerBuilder()
builder.service(
    "session",
    lambda: object(),
    lifetime="transient",
    scope="request",
)
container = builder.build()
