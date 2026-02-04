import asyncio
import warnings
import msgpack
import redis.asyncio as redis
import pydantic
import time

from contextvars import ContextVar
from .localproxy import LocalProxy

from .transports import Transport

from .decorators import META_KEY, Expose, Event, IdentityLoader

from .ctx import _scope_ctx, _active_transport_ctx, _active_user_ctx

import typing
from typing import Optional, Callable, Any, List, Set, Dict
import inspect

CHANNEL_NAME = "ephaptic:broadcast"

F = typing.TypeVar('F', bound=Callable[..., Any])

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, Set[Transport]] = {} # Map[user_id, Set[Transport]]
        self.redis: Optional[redis.Redis] = None

    def init_redis(self, url: str):
        self.redis = redis.from_url(url)

    def add(self, user_id: str, transport: Transport):
        if user_id not in self.active: self.active[user_id] = set()
        self.active[user_id].add(transport)

    def remove(self, user_id: str, transport: Transport):
        if user_id in self.active:
            self.active[user_id].discard(transport)
            if not self.active[user_id]: del self.active[user_id]

    async def broadcast(self, user_ids: List[str], event_name: str, args: list, kwargs: dict):
        payload = msgpack.dumps({
            "target_users": user_ids,
            "type": "event",
            "name": event_name,
            "payload": {"args": args, "kwargs": kwargs}
        })

        if self.redis: await self.redis.publish(CHANNEL_NAME, payload)
        else: await self._send(user_ids, payload)

    async def _send(self, user_ids: list[str], payload: bytes):
        for user_id in user_ids:
            if user_id in self.active:
                for transport in list(self.active[user_id]):
                    asyncio.create_task(self._safe_send(transport, payload))

    async def _safe_send(self, transport: Transport, payload: bytes):
        try:
            await transport.send(payload)
        except: ...

    async def start_redis(self):
        if not self.redis: return
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(CHANNEL_NAME)
        async for message in pubsub.listen():
            if message['type'] == 'message':
                data = msgpack.loads(message['data'])
                targets = data.get('target_users', [])
                await self._send(targets, message['data'])

manager = ConnectionManager()

_EXPOSED_FUNCTIONS = {}
_EXPOSED_EVENTS = {}
_IDENTITY_LOADER: Optional[Callable] = None
_HTTP_IDENTITY_LOADER: Optional[Callable] = None

_LOCAL_RATELIMIT_CACHE: Dict[str, List] = {} # [hits, expire_at]
# if redis isn't set up, assume that this is the only instance [no 'multiple nodes'] so ratelimits can be stored in memory.
# only used when Redis isn't set.
_LAST_CACHE_CLEANUP = time.time() # for manual cleaning up of the cache

class RatelimitExceededException(Exception):
    retry_after: int

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after

class EphapticTarget:
    def __init__(self, user_ids: list[str]):
        self.user_ids = user_ids

    async def emit(self, event_instance: pydantic.BaseModel):
        event_name = event_instance.__class__.__name__
        payload = event_instance.model_dump(mode='json')
        await manager.broadcast(
            self.user_ids,
            event_name,
            args=[],
            kwargs=payload,
        )

    def __getattr__(self, name: str):
        async def emitter(*args, **kwargs):
            await manager.broadcast(self.user_ids, name, list(args), dict(kwargs))
        return emitter
    
def _set_identity_loader(f):
    global _IDENTITY_LOADER
    _IDENTITY_LOADER = f

def _set_http_identity_loader(f):
    global _HTTP_IDENTITY_LOADER
    _HTTP_IDENTITY_LOADER = f

expose = Expose(_EXPOSED_FUNCTIONS)
event = Event(_EXPOSED_EVENTS)
identity_loader = IdentityLoader(_set_identity_loader)
http_identity_loader = IdentityLoader(_set_http_identity_loader)

class Ephaptic:
    _exposed_functions: Dict[str, Callable] = {}
    _exposed_events: Dict[str, typing.Type[pydantic.BaseModel]]
    _identity_loader: Optional[Callable] = None
    _http_identity_loader: Optional[Callable] = None

    expose: Expose
    event: Event
    identity_loader: IdentityLoader
    http_identity_loader: IdentityLoader

    def _async(self, func: Callable):
        async def wrapper(*args, **kwargs) -> Any:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        return wrapper

    def __init__(self):
        ...

    @classmethod
    def from_app(cls, app, path="/_ephaptic", redis_url=None):
        # `app` could be ~Flask~, Quart, FastAPI, etc.
        instance = cls()

        if redis_url:
            manager.init_redis(redis_url)

        module = app.__class__.__module__.split(".")[0]

        match module:
            case "quart":
                from .ext.quart.adapter import QuartAdapter
                adapter = QuartAdapter(instance, app, path, manager)
            case "fastapi":
                from .ext.fastapi.adapter import FastAPIAdapter
                adapter = FastAPIAdapter(instance, app, path, manager)
            case _:
                raise TypeError(f"Unsupported app type: {module}")
            
        instance._exposed_functions = _EXPOSED_FUNCTIONS.copy()
        instance._exposed_events = _EXPOSED_EVENTS.copy()
        instance._identity_loader = _IDENTITY_LOADER
        instance._http_identity_loader = _HTTP_IDENTITY_LOADER

        instance.expose = Expose(instance._exposed_functions)
        instance.event = Event(instance._exposed_events)
        instance.identity_loader = IdentityLoader(lambda f: setattr(instance, '_identity_loader', f))
        instance.http_identity_loader = IdentityLoader(lambda f: setattr(instance, '_http_identity_loader', f))

        return instance
    
    async def _check_ratelimit(self, func_name: str, limit: tuple[int, int], uid: str = None, ip: str = None):
        max_reqs, window = limit
        identifier = f'u:{uid}' if uid else f'ip:{ip}'
        now = time.time()
        current_window = int(now // window)
        reset = (current_window + 1) * window
        key = f'ephaptic:rl:{func_name}:{identifier}:{current_window}'

        if manager.redis:
            pipe = manager.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window + 1)
            results = await pipe.execute()
            hits = results[0]
        else:
            global _LAST_CACHE_CLEANUP
            if (now - _LAST_CACHE_CLEANUP) > 60:
                for k in [
                    k for k, v in _LOCAL_RATELIMIT_CACHE.items()
                    if v[1] < now
                ]: del _LOCAL_RATELIMIT_CACHE[k]
                _LAST_CACHE_CLEANUP = now

            entry = _LOCAL_RATELIMIT_CACHE.get(key)
            if not entry:
                entry = [0, reset]
                _LOCAL_RATELIMIT_CACHE[key] = entry

            entry[0] += 1
            hits = entry[0]

        if hits > max_reqs:
            retry_after = max(1, int(reset - now))
            raise RatelimitExceededException(f'Rate Limit exceeded. Try again in {retry_after} seconds.', retry_after=retry_after)

    
    def to(self, *args):
        targets = []
        for arg in args:
            if isinstance(arg, list): targets.extend(arg)
            else: targets.append(arg)
        return EphapticTarget(targets)
       
    async def emit(self, event_instance: pydantic.BaseModel):
        event_name = event_instance.__class__.__name__
        payload = event_instance.model_dump(mode='json')
        transport: Transport = _active_transport_ctx.get()
        if not transport:
            raise RuntimeError(
                f".emit({event_name}) called outside RPC context."
                f"Use .to(...).emit({event_name}) to broadcast from background tasks, to specific user(s)."
            )
        
        # NOTE: There is slight duplication here and in the EphapticTarget. Perhaps make these functions internally route to EphapticTargets but pass the transport to use?
        
        await transport.send(msgpack.dumps({
            'type': 'event',
            'name': event_name,
            'payload': {'args': [], 'kwargs': payload}
        }))
    
    async def handle_transport(self, transport: Transport):
        current_uid = None
        try:
            raw = await transport.receive()
            init = msgpack.loads(raw)

            if init.get('type') == 'init':
                try:
                    if self._identity_loader:
                        current_uid = await self._async(self._identity_loader)(init.get('auth'))
                    
                    if current_uid:
                        _active_user_ctx.set(current_uid)
                        manager.add(current_uid, transport)
                    else:
                        pass
                except Exception:
                    import traceback
                    traceback.print_exc()

            while True:
                raw = await transport.receive()
                data = msgpack.loads(raw)

                if data.get('type') == 'rpc':
                    call_id = data.get('id')
                    func_name = data.get('name')
                    args = data.get('args', [])
                    kwargs = data.get('kwargs', {}) # Note: Only Python client (currently) sends these, JS client does not.

                    if func_name in self._exposed_functions:
                        target_func = self._exposed_functions[func_name]
                        meta = getattr(target_func, META_KEY, {})

                        if meta.get('rate_limit'):
                            try:
                                await self._check_ratelimit(
                                    func_name,
                                    meta.get('rate_limit'),
                                    uid=current_uid,
                                    ip=transport.remote_addr,
                                )
                            except RatelimitExceededException as e:
                                await transport.send(msgpack.dumps({
                                    "id": call_id,
                                    "error": {
                                        "code": "RATELIMIT",
                                        "message": str(e),
                                        "data": { "retry_after": e.retry_after },
                                    },
                                }))
                                continue


                        hints = meta.get('hints') or typing.get_type_hints(target_func)
                        sig = meta.get('sig') or inspect.signature(target_func)                      
                        
                        try:
                            bound = sig.bind(*args, **kwargs)
                            bound.apply_defaults()
                        except TypeError as e:
                            await transport.send(msgpack.dumps({"id": call_id, "error": str(e)}))
                            continue

                        fields = {}
                        for name, param in sig.parameters.items():
                            if name in hints:
                                fields[name] = (hints[name], param.default if param.default is not inspect.Parameter.empty else ...)
                            else:
                                fields[name] = (Any, param.default if param.default is not inspect.Parameter.empty else ...)

                        DynamicInputModel = pydantic.create_model(f'DynamicInputModel_{func_name}', **fields)

                        try:
                            validated_data = DynamicInputModel(**bound.arguments)
                            final_arguments = validated_data.model_dump()
                        except pydantic.ValidationError as e:
                            await transport.send(msgpack.dumps({
                                "id": call_id,
                                "error": {
                                    "code": "VALIDATION_ERROR",
                                    "message": "Input validation failed.",
                                    "data": e.errors(),
                                },
                            }))
                            continue
                        
                        token_transport = _active_transport_ctx.set(transport)
                        token_user = _active_user_ctx.set(current_uid)
                        token_scope = _scope_ctx.set('rpc')

                        try:
                            result = await self._async(target_func)(**final_arguments)

                            return_type = meta.get('response_model') or hints.get("return", typing.Any)
                            if return_type and return_type is not inspect.Signature.empty and return_type is not typing.Any:
                                try:
                                    adapter = pydantic.TypeAdapter(return_type)
                                    validated = adapter.validate_python(result, from_attributes=True)
                                    result = adapter.dump_python(validated, mode='json')
                                except Exception as e:
                                    # Should we really treat this separately?
                                    # For input it's understandable, but for server responses it feels like a server issue.
                                    # Let's just return a RETURN_VALIDATION_ERROR and print the traceback.
                                    import traceback
                                    traceback.print_exc()
                                    await transport.send(msgpack.dumps({
                                        "id": call_id,
                                        "error": {
                                            "code": "RETURN_VALIDATION_ERROR",
                                            "message": f"Server returned invalid type: {e}",
                                            "data": None,
                                        },
                                    }))
                                    continue
                            elif isinstance(result, pydantic.BaseModel):
                                result = result.model_dump(mode='json')

                            await transport.send(msgpack.dumps({"id": call_id, "result": result}))
                        except Exception as e:
                            await transport.send(msgpack.dumps({"id": call_id, "error": str(e)}))
                        finally:
                            _active_transport_ctx.reset(token_transport)
                            _active_user_ctx.reset(token_user)
                            _scope_ctx.reset(token_scope)
                    else:
                        await transport.send(msgpack.dumps({
                            "id": call_id, 
                            "error": f"Function '{func_name}' not found."
                        }))
        except (asyncio.CancelledError, Transport.ConnectionClosed):
            ...
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if current_uid: manager.remove(current_uid, transport)
