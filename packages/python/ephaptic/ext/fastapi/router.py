from typing import *
from functools import wraps
import inspect
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder
from ...ephaptic import Ephaptic, RatelimitExceededException, expose
from ...ctx import is_http, is_rpc, active_user
from ...utils import parse_limit

class Router(APIRouter):
    ephaptic: Optional[Ephaptic]

    def __init__(self, ephaptic: Optional[Ephaptic] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ephaptic = ephaptic

    def bind(self, ephaptic: Ephaptic):
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
                if not self.ephaptic:
                    raise RuntimeError(f"Router for {path} is not bound to an Ephaptic instance. You must either call `.bind(ephaptic)`, or pass the `ephaptic` instance when constructing the Router.")
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

            if not self.ephaptic:
                raise RuntimeError(f"Router for {path} is not bound to an Ephaptic instance. You must either call `.bind(ephaptic)`, or pass the `ephaptic` instance when constructing the Router.")
            
            result = await self.ephaptic._async(func)(*args, **kwargs)

            if is_http():
                is_async_gen = inspect.isasyncgen(result)
                is_sync_gen = inspect.isgenerator(result)

                if is_async_gen or is_sync_gen:
                    async def generator():
                        if is_async_gen:
                            async for chunk in result:
                                yield json.dumps(jsonable_encoder(chunk)) + '\n'
                            
                        else:
                            def next_(gen):
                                try: return next(gen), False
                                except StopIteration: return None, True

                            while True:
                                chunk, done = await asyncio.to_thread(next_, result)
                                if done: break
                                yield json.dumps(jsonable_encoder(chunk)) + '\n'

                    return StreamingResponse(generator(), media_type='application/jsonl')
                
            return result
        
        deps = kwargs.pop('dependencies', [])
        if limit: deps.append(Depends(http_rl_dep))

        hint = get_type_hints(func).get('return')
        if hint:
            origin = get_origin(hint)
            origin_name = getattr(origin, '__name__', '')
            if origin in (AsyncGenerator, Generator, AsyncIterable, Iterable) or origin_name in ('AsyncGenerator', 'Generator', 'AsyncIterable', 'Iterable'):
                kwargs.setdefault('response_model', None)
            # Fixes a bug where FastAPI attempts to turn the generator into a Pydantic model (and fails).


        self.add_api_route(
            path,
            wrapper,
            methods=methods,
            dependencies=deps,
            **kwargs,
        )

        (self.ephaptic.expose if self.ephaptic else expose)(
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