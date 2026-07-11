"""Common pytest fixtures."""

import pytest

from container import ContainerBuilder


@pytest.fixture
def builder() -> ContainerBuilder:
    return ContainerBuilder()
