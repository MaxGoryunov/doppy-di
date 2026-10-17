"""Issue 148: compound locals shortened, fast path unchanged."""

from doppy_di import ContainerBuilder


def test_compile_get_fast_path_stable() -> None:
    builder = ContainerBuilder()
    builder.value("a", 1)
    builder.service("b", lambda a: a + 1, deps=["a"])
    container = builder.build()
    plan = container.compile()
    assert plan.get("b") == 2
    assert container.get("b") == 2


def test_child_override_helpers_stable() -> None:
    parent = ContainerBuilder().value("x", 1).build()
    child = parent.create_child(share_singletons=False)
    with child.override("x", 2):
        assert child.get("x") == 2
    assert parent.get("x") == 1


def test_tracer_and_session_scope_stable() -> None:
    events: list[str] = []
    builder = ContainerBuilder()
    builder.value("v", 5)
    container = builder.build()
    container.set_tracer(lambda *args: events.append(str(args)))
    assert container.get("v") == 5
