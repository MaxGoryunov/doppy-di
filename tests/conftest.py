"""Common pytest fixtures."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from doppy_di.container import ContainerBuilder


@pytest.fixture
def builder() -> ContainerBuilder:
    return ContainerBuilder()


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run one coroutine; single throwing call for exception tests (S5778)."""
    return asyncio.run(coro)
