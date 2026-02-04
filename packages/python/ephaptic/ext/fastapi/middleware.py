from starlette.middleware.base import BaseHTTPMiddleware
from ...ctx import _scope_ctx
from ...ephaptic import Ephaptic, _active_user_ctx

class CtxMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ephaptic: Ephaptic):
        super().__init__(app)
        self.ephaptic = ephaptic


    async def dispatch(self, request, call_next):
        token = _scope_ctx.set('http')
        user_token = None

        if self.ephaptic._http_identity_loader:
            user = await self.ephaptic._async(self.ephaptic._http_identity_loader)(request)
            if user:
                user_token = _active_user_ctx.set(user)

        try:
            return await call_next(request)
        finally:
            _scope_ctx.reset(token)
            if user_token: _active_user_ctx.reset(user_token)