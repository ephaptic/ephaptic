from typing import *
from functools import wraps
import inspect

from fastapi import APIRouter, Depends, HTTPException, Request
from ...ephaptic import Ephaptic, RatelimitExceededException
from ...ctx import is_http, is_rpc, active_user
from ...utils import parse_limit

class Router(APIRouter):
    ephaptic: Ephaptic

    def __init__(self, ephaptic: Ephaptic, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ephaptic = ephaptic

    def _register(
        self,
        func: Callable,
        methods: List[str],
        path: str,
        limit: Optional[str] = None,
        auth: bool = False,
        **kwargs,
    ):
        limit_config = parse_limit(limit) if limit else None

        async def http_rl_dep(req: Request):
            if limit_config:
                try:
                    await self.ephaptic._check_ratelimit(
                        func.__name__,
                        limit_config,
                        ip=req.client.host
                    )
                except RatelimitExceededException as e:
                    raise HTTPException(status_code=429, detail=str(e), headers={'X-Retry-After': e.retry_after})

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if auth and active_user() is None:
                raise Exception('Unauthorized') if is_rpc() else HTTPException(status_code=401, detail='Unauthorized')
            
            return await self.ephaptic._async(func)(*args, **kwargs)
        
        deps = kwargs.pop('dependencies', [])
        if limit: deps.append(Depends(http_rl_dep))

        self.add_api_route(
            path,
            wrapper,
            methods=methods,
            dependencies=deps,
            **kwargs,
        )

        self.ephaptic.expose(
            name=func.__name__,
            rate_limit=limit,
            hints=get_type_hints(func),
            sig=inspect.signature(func), # for bypassing the @wraps
        )(wrapper)

    def get    (self, path, limit=None, requires_login=False):
        def decorator(func): return self._register(func=func, methods=["GET"],    path=path, limit=limit, auth=requires_login)
        return decorator
    
    def post   (self, path, limit=None, requires_login=False):
        def decorator(func): return self._register(func=func, methods=["POST"],   path=path, limit=limit, auth=requires_login)
        return decorator

    def put    (self, path, limit=None, requires_login=False):
        def decorator(func): return self._register(func=func, methods=["PUT"],    path=path, limit=limit, auth=requires_login)
        return decorator

    def delete (self, path, limit=None, requires_login=False):
        def decorator(func): return self._register(func=func, methods=["DELETE"], path=path, limit=limit, auth=requires_login)
        return decorator
    
    def patch  (self, path, limit=None, requires_login=False):
        def decorator(func): return self._register(func=func, methods=["PATCH"],  path=path, limit=limit, auth=requires_login)
        return decorator