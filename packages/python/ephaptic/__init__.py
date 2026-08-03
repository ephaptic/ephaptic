from .ephaptic import (
    Ephaptic,
    expose,
    identity_loader,
    http_identity_loader,
    event,
    exception_handler,
    error,
)

from .errors import (
    ServiceError,
    RatelimitExceededException,
    EphapticError,
)

from .client import (
    connect,
)

from .ctx import (
    is_http,
    is_rpc,
    active_user,
)

# HACKY but works, and probably isn't even type-safe
def __getattr__(name: str):
    # expose the FastAPI Router at the top level (`from ephaptic import Router`),
    # without importing FastAPI for users who don't need it.
    # ephaptic is FastAPI-first, so this is the recommended path for HTTP + RPC routes.
    if name == "Router":
        from .ext.fastapi import Router
        return Router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
