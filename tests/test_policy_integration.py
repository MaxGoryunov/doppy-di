"""Policy integration tests with cache behavior."""

from container import ContainerBuilder
from devkit.nested import NestedRules, SameValuePolicy
from devkit.policy import ChildrenFirstPolicy, ParentFirstPolicy, UnorderedPolicy
from devkit.validation import ValidatingContainer, ValidationRunner


def test_children_first_keeps_singleton_identity():
    calls = 0

    def make_db():
        nonlocal calls
        calls += 1
        return {"db": calls}

    builder = ContainerBuilder()
    builder.service("db", make_db, lifetime="singleton")
    base = builder.build()

    container = ValidatingContainer(base, ChildrenFirstPolicy(), ValidationRunner(), None)

    a = container.get("db")
    b = container.get("db")

    assert a is b
    assert calls == 1


def test_parent_first_scope_identity():
    calls = 0

    def make_item():
        nonlocal calls
        calls += 1
        return {"item": calls}

    builder = ContainerBuilder()
    builder.service("item", make_item, lifetime="transient")
    base = builder.build()

    container = ValidatingContainer(base, ParentFirstPolicy(), ValidationRunner(), None)

    s1 = container.scope("s1")
    s2 = container.scope("s2")

    a1 = s1.get("item")
    a2 = s1.get("item")
    b1 = s2.get("item")

    assert a1 is a2
    assert a1 is not b1


def test_unordered_no_extra_effect():
    builder = ContainerBuilder()
    builder.service("child", lambda: 1)
    builder.service("parent", lambda child: {"child": child}, deps=["child"])
    base = builder.build()

    container = ValidatingContainer(base, UnorderedPolicy(), ValidationRunner(), None)

    obj = container.get("parent")
    assert obj["child"] == 1


def test_nested_validation_value_policy():
    builder = ContainerBuilder()
    builder.service("url", lambda: "postgresql://localhost/app", lifetime="singleton")
    builder.service("db", lambda url: {"url": url}, deps=["url"], lifetime="singleton")
    base = builder.build()

    nested = NestedRules()
    nested.same_policy = SameValuePolicy()

    container = ValidatingContainer(base, ChildrenFirstPolicy(), ValidationRunner(), nested)

    db = container.get("db")
    assert db["url"] == "postgresql://localhost/app"
