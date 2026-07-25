"""Common pytest fixtures."""

import pytest

from doppy_di.container import ContainerBuilder


@pytest.fixture
def builder() -> ContainerBuilder:
    return ContainerBuilder()
