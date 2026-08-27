"""Compare guardless fast path (#120) vs previous plan paths vs Injex.

Graphs:
  A) register-user: 6 objects, 2 singletons (Settings, ApiClient), root
     RegisterUser(transient). Primary focus per issue #46/#120.
  B) singleton chain: Settings(singleton) -> ApiClient(singleton) root.
  C) shared-singleton 2-leaf: root transient over two transient leaves, each
     leaf pulling 2 singleton deps (exercises literal2 flat templates).

Timing protocol mirrors benchmarks/compare_44.py (GC off, warmup, median).
"""

from __future__ import annotations

import gc
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable

from injex import Container as InjexContainer

from doppy_di import ContainerBuilder
from doppy_di import _legacy as legacy


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///:memory:"


class ApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings


class UserRepository:
    def __init__(self, client: ApiClient):
        self.client = client


class EmailSender:
    def __init__(self, client: ApiClient):
        self.client = client


class AuditLog:
    def __init__(self, settings: Settings):
        self.settings = settings


class RegisterUser:
    def __init__(self, repo: UserRepository, email: EmailSender, audit: AuditLog):
        self.repo = repo
        self.email = email
        self.audit = audit


settings = Settings()
manual_client = ApiClient(settings)


def manual_register() -> RegisterUser:
    return RegisterUser(
        UserRepository(manual_client),
        EmailSender(manual_client),
        AuditLog(settings),
    )


def _register_container(builder: ContainerBuilder) -> None:
    builder.value(Settings, settings)
    builder.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    builder.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    builder.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    builder.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    builder.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )


def setup_register() -> dict[str, Callable[[], Any]]:
    b = ContainerBuilder()
    _register_container(b)
    c = b.build()
    pnew = c.compile()
    pfrozen = c.compile(allow_post_compile_overrides=False)
    pfast = c.compile(guardless=True)
    pleg = legacy.LegacyExecutionPlan.from_container(c)

    inj = InjexContainer()
    inj.add_instance(Settings, settings)
    inj.add_singleton(ApiClient)
    inj.add_transient(UserRepository)
    inj.add_transient(EmailSender)
    inj.add_transient(AuditLog)
    inj.add_transient(RegisterUser)
    inj.assert_valid()
    inj.resolve(RegisterUser)

    return {
        "manual": manual_register,
        "injex.resolve": lambda: inj.resolve(RegisterUser),
        "legacy plan.get": lambda: pleg.get(RegisterUser),
        "legacy bound()": pleg.bind(RegisterUser),
        "new plan.get": lambda: pnew.get(RegisterUser),
        "new bound()": pnew.bind(RegisterUser),
        "frozen plan.get": lambda: pfrozen.get(RegisterUser),
        "frozen bound()": pfrozen.bind(RegisterUser),
        "fast plan.get (guardless)": lambda: pfast.get(RegisterUser),
        "fast bound() (guardless)": pfast.bind(RegisterUser),
    }


# --- Graph B: singleton chain -------------------------------------------------


@dataclass(frozen=True)
class Config:
    host: str = "db"


class Conn:
    def __init__(self, cfg: Config):
        self.cfg = cfg


class Service:
    def __init__(self, conn: Conn):
        self.conn = conn


def setup_chain() -> dict[str, Callable[[], Any]]:
    b = ContainerBuilder()
    b.value(Config, Config())
    b.service(Conn, Conn, lifetime="singleton", deps=[Config])
    b.service(Service, Service, lifetime="singleton", deps=[Conn])
    c = b.build()
    pnew = c.compile()
    pfrozen = c.compile(allow_post_compile_overrides=False)
    pfast = c.compile(guardless=True)

    inj = InjexContainer()
    inj.add_instance(Config, Config())
    inj.add_singleton(Conn)
    inj.add_singleton(Service)
    inj.assert_valid()
    inj.resolve(Service)

    return {
        "manual": lambda: Service(Conn(Config())),
        "injex.resolve": lambda: inj.resolve(Service),
        "new plan.get": lambda: pnew.get(Service),
        "frozen plan.get": lambda: pfrozen.get(Service),
        "fast plan.get (guardless)": lambda: pfast.get(Service),
        "fast bound() (guardless)": pfast.bind(Service),
    }


class DepA:
    pass


class DepB:
    pass


class LeafX:
    def __init__(self, a: DepA, s: Settings):
        self.a = a
        self.s = s


class LeafY:
    def __init__(self, b: DepB, s: Settings):
        self.b = b
        self.s = s


class Root:
    def __init__(self, x: LeafX, y: LeafY):
        self.x = x
        self.y = y


shared_settings = Settings()


def setup_leaf2() -> dict[str, Callable[[], Any]]:
    b = ContainerBuilder()
    b.value(DepA, DepA())
    b.value(DepB, DepB())
    b.value(Settings, shared_settings)
    b.service(LeafX, LeafX, lifetime="transient", deps=[DepA, Settings])
    b.service(LeafY, LeafY, lifetime="transient", deps=[DepB, Settings])
    b.service(Root, Root, lifetime="transient", deps=[LeafX, LeafY])
    c = b.build()
    pfrozen = c.compile(allow_post_compile_overrides=False)
    pfast = c.compile(guardless=True)
    pleg = legacy.LegacyExecutionPlan.from_container(c)
    return {
        "manual": lambda: Root(LeafX(DepA(), shared_settings), LeafY(DepB(), shared_settings)),
        "legacy plan.get": lambda: pleg.get(Root),
        "frozen plan.get": lambda: pfrozen.get(Root),
        "fast plan.get (guardless)": lambda: pfast.get(Root),
        "fast bound() (guardless)": pfast.bind(Root),
    }


def bench(
    fn: Callable[[], Any],
    warmup: int = 150_000,
    iters: int = 400_000,
    samples: int = 11,
) -> float:
    for _ in range(1000):
        fn()
    for _ in range(warmup):
        fn()
    meds = []
    for _ in range(samples):
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        dt = time.perf_counter() - t0
        meds.append(dt / iters)
    return statistics.median(meds) * 1e6  # us/op


def report(title: str, cases: dict[str, Callable[[], Any]]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'impl':<30} {'median us/op':>12}")
    rows = [(name, bench(fn)) for name, fn in cases.items()]
    baseline = rows[0][1]
    for name, us in rows:
        label = name
        if name == "manual":
            label = "manual (reference)"
        print(f"{label:<30} {us:>12.3f}  ({us / baseline:.2f}x manual)")


def main() -> None:
    gc.disable()
    report("A) register-user (6 obj, 2 singletons)", setup_register())
    report("B) singleton chain root", setup_chain())
    report("C) shared-singleton two-leaf literal2", setup_leaf2())


if __name__ == "__main__":
    main()
