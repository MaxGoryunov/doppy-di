"""Tests for nested policies, scopes and overrides."""

import pytest

from container import Container, ContainerBuilder, CycleError, NestedRuleError, Rule
from devkit.nested import NestedRules, SameObjectPolicy, SameValuePolicy
from devkit.policy import ChildrenFirstPolicy, ParentFirstPolicy, UnorderedPolicy
from devkit.validation import ValidatingContainer, ValidationRunner


class Database:
    """Database example.

    Example:
        >>> db = Database("sqlite://")
        >>> db.url
        'sqlite://'
    """

    def __init__(self, url: str) -> None:
        self.url = url


class Repo:
    """Repository example.

    Example:
        >>> repo = Repo(Database("sqlite://"))
        >>> repo.db.url
        'sqlite://'
    """

    def __init__(self, db: Database) -> None:
        self.db = db


class Service:
    """Service example.

    Example:
        >>> service = Service(Repo(Database("sqlite://")))
        >>> service.repo.db.url
        'sqlite://'
    """

    def __init__(self, repo: Repo) -> None:
        self.repo = repo


def make_container() -> Container:
    builder = ContainerBuilder()
    builder.service("url", lambda: "postgresql://localhost/app", lifetime="singleton")
    builder.service("db", lambda url: Database(url), deps=["url"], lifetime="singleton")
    builder.service("repo", lambda db: Repo(db), deps=["db"], lifetime="transient")
    builder.service("service", lambda repo: Service(repo), deps=["repo"], lifetime="transient")
    return builder.build()


def make_nested(base: Container) -> NestedRules:
    nested = NestedRules()
    nested.add_nested(
        "service",
        "repo",
        Rule(("service", "repo"), lambda repo: repo, lifetime="transient", deps=("repo",)),
        base.config.ruleset,
    )
    nested.add_nested(
        "repo",
        "db",
        Rule(("repo", "db"), lambda db: db, lifetime="transient", deps=("db",)),
        base.config.ruleset,
    )
    return nested


def test_nested_same_object_policy() -> None:
    base = make_container()
    nested = make_nested(base)
    nested.same_policy = SameObjectPolicy()

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"service": ["repo"], "repo": ["db"]}),
        ValidationRunner(),
        nested,
    )

    service = container.get("service")
    repo = container.get(("service", "repo"))
    db = container.get(("repo", "db"))

    assert service.repo is repo
    assert repo.db is db
    assert service.repo.db is db


def test_nested_same_value_policy() -> None:
    base = make_container()
    nested = make_nested(base)
    nested.same_policy = SameValuePolicy()

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"service": ["repo"], "repo": ["db"]}),
        ValidationRunner(),
        nested,
    )

    service = container.get("service")
    assert service.repo.db.url == "postgresql://localhost/app"
    assert container.get(("repo", "db")).url == service.repo.db.url


def test_nested_scope_isolated() -> None:
    base = make_container()
    nested = make_nested(base)

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"service": ["repo"], "repo": ["db"]}),
        ValidationRunner(),
        nested,
    )

    s1 = container.scope("s1")
    s2 = container.scope("s2")

    a = s1.get("service")
    b = s1.get("service")
    c = s2.get("service")

    assert a is b
    assert a is not c
    assert a.repo is b.repo
    assert a.repo is not c.repo


def test_override_changes_nested_resolution() -> None:
    base = make_container()
    nested = make_nested(base)

    container = ValidatingContainer(
        base,
        ChildrenFirstPolicy(nested={"service": ["repo"], "repo": ["db"]}),
        ValidationRunner(),
        nested,
    )

    fake_db = Database("mock://db")

    with container.override("db", fake_db):
        service = container.get("service")
        assert service.repo.db is fake_db

    service = container.get("service")
    assert service.repo.db.url == "postgresql://localhost/app"


def test_nested_validation_failure() -> None:
    base = make_container()
    nested = make_nested(base)
    nested.same_policy = SameObjectPolicy()
    nested.add_nested(
        "service",
        "missing",
        Rule(("service", "missing"), lambda: Repo(Database("broken://db")), lifetime="transient"),
        base.config.ruleset,
    )

    container = ValidatingContainer(
        base,
        UnorderedPolicy(),
        ValidationRunner(),
        nested,
    )

    with pytest.raises(NestedRuleError):
        container.get("service")


def test_unordered_policy_keeps_basic_resolution() -> None:
    base = make_container()
    nested = make_nested(base)

    container = ValidatingContainer(
        base,
        UnorderedPolicy(),
        ValidationRunner(),
        nested,
    )

    service = container.get("service")
    assert isinstance(service, Service)
    assert isinstance(service.repo, Repo)
    assert isinstance(service.repo.db, Database)


def test_parent_first_policy_resolves_children_after_parent() -> None:
    base = make_container()
    nested = make_nested(base)

    container = ValidatingContainer(
        base,
        ParentFirstPolicy(nested={"service": ["repo"], "repo": ["db"]}),
        ValidationRunner(),
        nested,
    )

    service = container.get("service")
    assert isinstance(service, Service)
    assert isinstance(service.repo, Repo)
    assert isinstance(service.repo.db, Database)


def test_same_value_policy_with_broken_eq() -> None:
    class Broken:
        def __init__(self, val: int) -> None:
            self.val = val

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("eq broken")

    policy = SameValuePolicy()
    assert policy.check(Broken(1), Broken(1)) is False


def test_same_value_policy_with_broken_eq_strict() -> None:
    class Broken:
        def __init__(self, val: int) -> None:
            self.val = val

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("eq broken")

    policy = SameValuePolicy(strict=True)
    with pytest.raises(RuntimeError, match="eq broken"):
        policy.check(Broken(1), Broken(1))


def test_same_value_policy_with_none() -> None:
    """None == None is True, should not crash."""
    policy = SameValuePolicy()
    assert policy.check(None, None)


def test_same_value_policy_with_different_types() -> None:
    """1 == True in Python, but this may be surprising."""
    policy = SameValuePolicy()
    assert policy.check(1, True)


def test_cycle_detection_blocks_nested_graph() -> None:
    builder = ContainerBuilder()

    builder.service("a", lambda b: {"b": b}, deps=["b"])
    with pytest.raises(CycleError):
        builder.service("b", lambda a: {"a": a}, deps=["a"])
