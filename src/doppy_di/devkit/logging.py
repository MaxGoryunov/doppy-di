"""Logging layer for container access.

Example:
    >>> from doppy_di.container import ContainerBuilder
    >>> builder = ContainerBuilder()
    >>> builder.value("x", 1)
    >>> base = builder.build()
    >>> wrapped = LoggingContainer(base, lambda msg: None)
    >>> wrapped.get("x")
    1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..container import Container, Key, OverrideContext, Scope


@dataclass(frozen=True)
class LoggingContainer:
    """Log container operations while preserving the same API.

    Example:
        >>> events = []
        >>> def log(msg):
        ...     events.append(msg)
        >>> from doppy_di.container import ContainerBuilder
        >>> builder = ContainerBuilder()
        >>> builder.value("x", 1)
        >>> base = builder.build()
        >>> wrapped = LoggingContainer(base, log)
        >>> wrapped.get("x")
        1
    """

    wrapped: Container
    log: Callable[[str], None]

    def get(self, key: Key) -> Any:
        """Resolve key and log the operation."""
        self.log(f"get({key!r})")
        try:
            obj = self.wrapped.get(key)
            self.log(f"get({key!r}) -> ok")
            return obj
        except BaseException as exc:
            self.log(f"get({key!r}) -> error: {exc.__class__.__name__}")
            raise

    def has(self, key: Key) -> bool:
        """Log presence check and delegate."""
        self.log(f"has({key!r})")
        return self.wrapped.has(key)

    def scope(self, name: str) -> Scope:
        """Log scope creation and delegate."""
        self.log(f"scope({name!r})")
        return self.wrapped.scope(name)

    def override(self, key: Key, value: Any) -> OverrideContext:
        """Log override creation and delegate."""
        self.log(f"override({key!r})")
        return self.wrapped.override(key, value)
