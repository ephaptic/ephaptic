import asyncio
import msgpack
import redis.asyncio as redis

from contextvars import ContextVar
from .localproxy import LocalProxy

from .transports import Transport

from typing import Optional, Callable, Any, List, Set, Dict
import inspect

_active_transport_ctx = ContextVar('active_transport', default=None)
_active_user_ctx = ContextVar('active_user', default=None)

active_user = LocalProxy(_active_user_ctx.get)

CHANNEL_NAME = "ephaptic:broadcast"

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
                    try:
                        await transport.send(payload)
                    except: pass

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

class EphapticTarget:
    def __init__(self, user_ids: list[str]):
        self.user_ids = user_ids

    def __getattr__(self, name: str):
        async def emitter(*args, **kwargs):
            await manager.broadcast(self.user_ids, name, list(args), dict(kwargs))
        return emitter
    
class Ephaptic:
    _exposed_functions: Dict[str, Callable] = {}
    _identity_loader: Optional[Callable] = None

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
        # `app` could be Flask, Quart, FastAPI, etc.
        instance = cls()

        if redis_url:
            manager.init_redis(redis_url)
            # TODO: framework-specific hooks for the background listener.

        module = app.__class__.__module__.split(".")[0]

        match module:
            case "quart":
                from .adapters.quart_ import QuartAdapter
                adapter = QuartAdapter(instance, app, path, manager)
            case "fastapi":
                from .adapters.fastapi_ import FastAPIAdapter
                adapter = FastAPIAdapter(instance, app, path, manager)
            case _:
                raise TypeError(f"Unsupported app type: {module}")
            
        return instance

            
    def expose(self, func: Callable):
        self._exposed_functions[func.__name__] = func
        return func
    
    def identity_loader(self, func: Callable):
        self._identity_loader = func
        return func
    
    def to(self, *args):
        targets = []
        for arg in args:
            if isinstance(arg, list): targets.extend(arg)
            else: targets.append(arg)
        return EphapticTarget(targets)
    
    def __getattr__(self, name: str):
        async def emitter(*args, **kwargs):
            transport: Transport = _active_transport_ctx.get()
            if not transport:
                raise RuntimeError(
                    f".{name}() called outside RPC context."
                    f"Use .to(...).{name}() to broadcast from background tasks, to specific user(s)."
                )
            
            await transport.send(msgpack.dumps({
                "type": "event",
                "name": name,
                "payload": {"args": list(args), "kwargs": dict(kwargs)},
            }))
        
        return emitter
    
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
                        token_transport = _active_transport_ctx.set(transport)
                        token_user = _active_user_ctx.set(current_uid)

                        try:
                            result = await self._async(target_func)(*args, **kwargs)
                            await transport.send(msgpack.dumps({"id": call_id, "result": result}))
                        except Exception as e:
                            await transport.send(msgpack.dumps({"id": call_id, "error": str(e)}))
                        finally:
                            _active_transport_ctx.reset(token_transport)
                            _active_user_ctx.reset(token_user)
                    else:
                        await transport.send(msgpack.dumps({
                            "id": call_id, 
                            "error": f"Function '{func_name}' not found."
                        }))
        except asyncio.CancelledError:
            ...
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if current_uid: manager.remove(current_uid, transport)