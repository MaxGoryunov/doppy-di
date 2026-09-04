"""Flask integration.

Provides ``setup_doppy`` which registers request/teardown hooks that create a
per-request scope and expose it via ``flask.g`` and request context.

Examples:
    >>> from flask import Flask
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.flask import setup_doppy
    >>> app = Flask(__name__)
    >>> services = Container()
    >>> setup_doppy(app, services)
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from doppy_di.container import Container


def setup_doppy(
    app: Any,
    container: Container,
    scope: str = "request",
) -> Any:
    """Register request-scope hooks on a Flask app.

    Args:
        app: Flask application.
        container: Container used for resolution.
        scope: Scope name created per request.

    Returns:
        The same app (hook registration is in place).
    """
    from flask import g  # type: ignore[import-not-found]

    @app.before_request  # type: ignore
    def _open_doppy_scope() -> None:
        g.doppy_scope = container.scope(scope).__enter__()
        g.doppy_scope.set_context("request", _current_request())

    @app.teardown_request  # type: ignore
    def _close_doppy_scope(_exc: Optional[BaseException]) -> None:
        s = getattr(g, "doppy_scope", None)
        if s is not None:
            s.__exit__(*sys.exc_info())

    return app


def _current_request() -> Any:
    from flask import request

    return request
