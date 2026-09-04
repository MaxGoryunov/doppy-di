"""gRPC integration.

Provides ``setup_doppy`` which returns a server interceptor opening a fresh
scope per RPC call.

Examples:
    >>> from doppy_di.container import Container
    >>> from doppy_di.ext.grpc import setup_doppy
    >>> services = Container()
    >>> interceptor = setup_doppy(services)
"""

from __future__ import annotations

import importlib
from typing import Any

from doppy_di.container import Container


def setup_doppy(
    container: Container,
    scope: str = "rpc",
) -> Any:
    """Return a gRPC server interceptor opening a per-RPC scope.

    Args:
        container: Container used for resolution.
        scope: Scope name created per RPC call.

    Returns:
        A ``grpc.ServerInterceptor`` instance. Pass it to
        ``grpc.server(interceptors=[...])``.
    """
    grpc: Any = importlib.import_module("grpc")

    class _DoppyInterceptor(grpc.ServerInterceptor):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()

        def intercept_service(self, continuation: Any, handler_call_details: Any) -> Any:
            def doppy_wrapper(request: Any, context: Any) -> Any:
                with container.scope(scope) as s:
                    s.set_context("rpc", handler_call_details)
                    return continuation(request, context)

            return doppy_wrapper

    return _DoppyInterceptor()
