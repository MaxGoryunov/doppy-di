"""Compare issue #44 changes: new plan vs saved legacy plan vs Injex."""

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


def manual() -> RegisterUser:
    return RegisterUser(
        UserRepository(manual_client),
        EmailSender(manual_client),
        AuditLog(settings),
    )


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


def main() -> None:
    # doppy new (current plan.py)
    cnew = ContainerBuilder()
    cnew.value(Settings, settings)
    cnew.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    cnew.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    cnew.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    cnew.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    cnew.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )
    pnew = cnew.build().compile()
    bound_new = pnew.bind(RegisterUser)

    # doppy legacy (saved HEAD plan.py)
    c_old = ContainerBuilder().build()
    c_old.value(Settings, settings)
    c_old.service(ApiClient, ApiClient, lifetime="singleton", deps=[Settings])
    c_old.service(UserRepository, UserRepository, lifetime="transient", deps=[ApiClient])
    c_old.service(EmailSender, EmailSender, lifetime="transient", deps=[ApiClient])
    c_old.service(AuditLog, AuditLog, lifetime="transient", deps=[Settings])
    c_old.service(
        RegisterUser,
        RegisterUser,
        lifetime="transient",
        deps=[UserRepository, EmailSender, AuditLog],
    )
    pleg = legacy.LegacyExecutionPlan.from_container(c_old)
    bound_legacy = pleg.bind(RegisterUser)

    # injex
    inj = InjexContainer()
    inj.add_instance(Settings, settings)
    inj.add_singleton(ApiClient)
    inj.add_transient(UserRepository)
    inj.add_transient(EmailSender)
    inj.add_transient(AuditLog)
    inj.add_transient(RegisterUser)
    inj.assert_valid()
    inj.resolve(RegisterUser)

    print(f"{'impl':<34} {'median us/op':>12}")
    print(f"{'manual':<34} {bench(manual):>12.3f}")
    print(f"{'injex.resolve':<34} {bench(lambda: inj.resolve(RegisterUser)):>12.3f}")
    print(f"{'legacy plan.get':<34} {bench(lambda: pleg.get(RegisterUser)):>12.3f}")
    print(f"{'legacy bound()':<34} {bench(bound_legacy):>12.3f}")
    print(f"{'new plan.get':<34} {bench(lambda: pnew.get(RegisterUser)):>12.3f}")
    print(f"{'new bound()':<34} {bench(bound_new):>12.3f}")


if __name__ == "__main__":
    gc.disable()
    main()
