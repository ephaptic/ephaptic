from typing import *
from functools import wraps
import inspect
import re

from fastapi import APIRouter, Depends, Request
from ...ephaptic import Ephaptic, expose, _EXPOSED_FUNCTIONS
from ...errors import ServiceError
from ...ctx import active_user
from ...utils import parse_limit

_PLACEHOLDER_NAMES = frozenset({'<lambda>', '<genexpr>', '<listcomp>', '<dictcomp>', '<setcomp>'})
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def derive_name(path: str) -> str:
    segments = []
    for segment in path.split('/'):
        segment = segment.strip()
        if not segment: continue
        if segment.startswith('{') and segment.endswith('}'): continue
        cleaned = re.sub(r'[^A-Za-z0-9]+', '_', segment).strip('_')
        if cleaned: segments.append(cleaned)
    return '_'.join(segments)


def _usable_identifier(name: Optional[str]) -> bool:
    return bool(name) and name not in _PLACEHOLDER_NAMES and bool(_IDENTIFIER.match(name))


class Router(APIRouter):
    ephaptic: Optional[Ephaptic]

    def __init__(self, ephaptic: Optional[Ephaptic] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ephaptic = ephaptic
        self._pending_names: set[str] = set()
        # rpc_name -> wrapper
        self._registered: dict[str, object] = {}

    def bind(self, ephaptic: Ephaptic):
        """
        Bind the Router to an Ephaptic instance.
        """
        self.ephaptic = ephaptic
        for rpc_name, func in self._registered.items():
            if rpc_name in ephaptic._exposed_functions: continue
            ephaptic._exposed_functions[rpc_name] = func

    def _resolve_name(self, func: Callable, path: str, name: Optional[str]) -> str:
        if name: return name
        own = getattr(func, '__name__', None)
        if _usable_identifier(own): return own
        derived = derive_name(path)
        if not derived:
            raise ValueError(
                f"Could not resolve an RPC name for '{path}'. "
                f"Pass a named function or the `name` option."
            )
        return derived

    def _register(
        self,
        func: Callable,
        methods: List[str],
        path: str,
        limit: Optional[str] = None,
        auth: bool = False,
        name: Optional[str] = None,
        **kwargs,
    ):
        limit_config = parse_limit(limit) if limit else None
        rpc_name = self._resolve_name(func, path, name)

        registry = self.ephaptic._exposed_functions if self.ephaptic else _EXPOSED_FUNCTIONS
        if rpc_name in registry or rpc_name in self._pending_names:
            raise ValueError(
                f"An RPC named '{rpc_name}' is already registered. "
                f"Give one of the colliding routes an explicit `name`; RPC dispatch "
                f"does not consider the HTTP method."
            )
        self._pending_names.add(rpc_name)

        def _require_binding():
            if not self.ephaptic:
                raise RuntimeError(
                    f"Router for {path} is not bound to an Ephaptic instance. You must either "
                    f"call `.bind(ephaptic)`, or pass the `ephaptic` instance when constructing the Router."
                )

        async def http_guard_dep(req: Request):
            _require_binding()

            if auth and active_user() is None:
                raise ServiceError('Unauthorized', code='UNAUTHORIZED', status_code=401)

            if limit_config:
                ip = req.client.host if req.client else None
                ip_header = getattr(self.ephaptic, 'ip_header', None)
                if ip_header:
                    forwarded = req.headers.get(ip_header)
                    if forwarded:
                        ip = forwarded.split(',')[0].strip()

                await self.ephaptic._check_ratelimit(
                    rpc_name,
                    limit_config,
                    uid=active_user(),
                    ip=ip,
                )

        def _pre():
            _require_binding()
            if auth and active_user() is None:
                raise ServiceError('Unauthorized', code='UNAUTHORIZED', status_code=401)

        if inspect.isasyncgenfunction(func) or inspect.isgeneratorfunction(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                _pre()
                if inspect.isasyncgenfunction(func):
                    async for chunk in await self.ephaptic._async(func)(*args, **kwargs):
                        yield chunk
                else:
                    gen = await self.ephaptic._async(func)(*args, **kwargs)
                    try:
                        for chunk in gen:
                            yield chunk
                    finally:
                        gen.close()
        else:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                _pre()
                return await self.ephaptic._async(func)(*args, **kwargs)

        deps = kwargs.pop('dependencies', [])
        deps.append(Depends(http_guard_dep))

        self.add_api_route(
            path,
            wrapper,
            methods=methods,
            dependencies=deps,
            **kwargs,
        )

        self._registered[rpc_name] = wrapper

        (self.ephaptic.expose if self.ephaptic else expose)(
            name=rpc_name,
            rate_limit=limit,
            requires_login=auth,
            hints=get_type_hints(func),
            sig=inspect.signature(func), # for bypassing the @wraps
        )(wrapper)

        return func

    def _verb(self, method, path, handler, options, limit, requires_login, name, kwargs):
        if options is not None:
            if not isinstance(options, dict):
                raise TypeError(
                    "The third positional argument to a Router verb must be an options "
                    "mapping; pass keyword arguments, or a dict of them."
                )
            unknown = set(options) - {'limit', 'rate_limit', 'requires_login', 'name'}
            if unknown:
                raise TypeError(
                    f"Unknown Router option(s): {', '.join(sorted(unknown))}. "
                    f"Valid options are: limit, requires_login, name."
                )
            limit = options.get('limit', options.get('rate_limit', limit))
            requires_login = options.get('requires_login', requires_login)
            name = options.get('name', name)

        def register(func):
            return self._register(
                func=func, methods=[method], path=path,
                limit=limit, auth=requires_login, name=name, **kwargs,
            )

        if handler is None:
            return register

        if not callable(handler):
            raise TypeError(
                "The second positional argument to a Router verb must be the handler. "
                "Did you mean to pass options as keyword arguments?"
            )
        return register(handler)

    def get(self, path, handler=None, options=None, *, limit=None, requires_login=False, name=None, **kwargs):
        return self._verb('GET', path, handler, options, limit, requires_login, name, kwargs)

    def post(self, path, handler=None, options=None, *, limit=None, requires_login=False, name=None, **kwargs):
        return self._verb('POST', path, handler, options, limit, requires_login, name, kwargs)

    def put(self, path, handler=None, options=None, *, limit=None, requires_login=False, name=None, **kwargs):
        return self._verb('PUT', path, handler, options, limit, requires_login, name, kwargs)

    def delete(self, path, handler=None, options=None, *, limit=None, requires_login=False, name=None, **kwargs):
        return self._verb('DELETE', path, handler, options, limit, requires_login, name, kwargs)

    def patch(self, path, handler=None, options=None, *, limit=None, requires_login=False, name=None, **kwargs):
        return self._verb('PATCH', path, handler, options, limit, requires_login, name, kwargs)