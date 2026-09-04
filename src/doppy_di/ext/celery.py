"""Celery integration.

Provides ``setup_doppy`` which connects task signals that open a fresh scope
per task execution.

Examples:
    >>> from celery import Celery
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.celery import setup_doppy
    >>> app = Celery("demo")
    >>> services = Container()
    >>> setup_doppy(app, services)
"""

from __future__ import annotations

import sys
from typing import Any, MutableMapping

from doppy_di.container import Container

_ACTIVE: MutableMapping[str, Any] = {}


def setup_doppy(
    app: Any,
    container: Container,
    scope: str = "task",
) -> Any:
    """Register per-task scope signals on a Celery app.

    Args:
        app: Celery application.
        container: Container used for resolution.
        scope: Scope name created per task.

    Returns:
        The same app (signals connected).
    """
    from celery import signals  # type: ignore[import-not-found]

    def _open(task_id: str, **kwargs: Any) -> None:
        scope_obj = container.scope(scope)
        scope_obj.set_context("task_id", task_id)
        _ACTIVE[task_id] = scope_obj.__enter__()

    def _close(task_id: str, **kwargs: Any) -> None:
        scope_obj = _ACTIVE.pop(task_id, None)
        if scope_obj is not None:
            scope_obj.__exit__(*sys.exc_info())

    signals.task_prerun.connect(_open, weak=False)
    signals.task_postrun.connect(_close, weak=False)
    return app
