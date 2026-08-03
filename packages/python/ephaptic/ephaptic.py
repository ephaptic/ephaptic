import asyncio
import redis.asyncio as redis
import pydantic
import time
import json
import logging

from .transports import Transport
from .encodings.msgpack import MsgpackEncoding

from .decorators import META_KEY, Expose, Event, IdentityLoader, ExceptionHandler, Error

from .ctx import _scope_ctx, _active_transport_ctx, _active_user_ctx

from .errors import ServiceError, RatelimitExceededException
from .utils import identity_key, UnsupportedIdentityError

logger = logging.getLogger('ephaptic')

import typing
from typing import Optional, Callable, Any, List, Set, Dict
import inspect

CHANNEL_NAME = "ephaptic:broadcast"

F = typing.TypeVar('F', bound=Callable[..., Any])

encoding = MsgpackEncoding()

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
        message = encoding.encode({
            "type": "event",
            "name": event_name,
            "payload": {"args": args, "kwargs": kwargs}
        })

        if self.redis:
            envelope = encoding.encode({
                "target_users": user_ids,
                "message": message,
            })
            await self.redis.publish(CHANNEL_NAME, envelope)
        else: await self._send(user_ids, message)

    async def _send(self, user_ids: list[str], message: bytes):
        for user_id in user_ids:
            if user_id in self.active:
                for transport in list(self.active[user_id]):
                    asyncio.create_task(self._safe_send(transport, message))

    async def _safe_send(self, transport: Transport, message: bytes):
        try: await transport.send(message)
        except Exception: ...

    async def start_redis(self):
        if not self.redis: return
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(CHANNEL_NAME)
        async for message in pubsub.listen():
            if message['type'] == 'message':
                envelope = encoding.decode(message['data'])
                targets = envelope.get('target_users', [])
                await self._send(targets, envelope['message'])

_EXPOSED_FUNCTIONS = {}
_EXPOSED_EVENTS = {}
_EXCEPTION_HANDLERS: Dict[type, Callable] = {}
_ERRORS: Dict[str, type] = {}
_IDENTITY_LOADER: Optional[Callable] = None
_HTTP_IDENTITY_LOADER: Optional[Callable] = None

_LOCAL_RATELIMIT_CACHE: Dict[str, List] = {} # [hits, expire_at]
# if redis isn't set up, assume that this is the only instance [no 'multiple nodes'] so ratelimits can be stored in memory.
# only used when Redis isn't set.
_LAST_CACHE_CLEANUP = time.time() # for manual cleaning up of the cache

class EphapticTarget:
    def __init__(self, user_ids: list[str], manager: ConnectionManager):
        self.user_ids = user_ids
        self.manager = manager

    async def emit(self, event_instance: pydantic.BaseModel):
        event_name = event_instance.__class__.__name__
        payload = event_instance.model_dump(mode='python')
        await self.manager.broadcast(
            self.user_ids,
            event_name,
            args=[],
            kwargs=payload,
        )

    def __getattr__(self, name: str):
        async def emitter(*args, **kwargs):
            await self.manager.broadcast(self.user_ids, name, list(args), dict(kwargs))
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
exception_handler = ExceptionHandler(_EXCEPTION_HANDLERS)
error = Error(_ERRORS)

class Ephaptic:
    _exposed_functions: Dict[str, Callable] = {}
    _exposed_events: Dict[str, typing.Type[pydantic.BaseModel]]
    _exception_handlers: Dict[type, Callable]
    _errors: Dict[str, type]
    _identity_loader: Optional[Callable] = None
    _http_identity_loader: Optional[Callable] = None

    expose: Expose
    event: Event
    identity_loader: IdentityLoader
    http_identity_loader: IdentityLoader
    exception_handler: ExceptionHandler
    error: Error

    debug: bool = False
    allowed_origins: Optional[List[str]] = None
    ip_header: Optional[str] = None
    _app: Any = None

    def _async(self, func: Callable):
        async def wrapper(*args, **kwargs) -> Any:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            elif inspect.isasyncgenfunction(func) or inspect.isgeneratorfunction(func):
                return func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        return wrapper

    def __init__(self):
        self.manager = ConnectionManager()
        self._exposed_functions = _EXPOSED_FUNCTIONS.copy()
        self._exposed_events = _EXPOSED_EVENTS.copy()
        self._exception_handlers = _EXCEPTION_HANDLERS.copy()
        self._errors = _ERRORS.copy()
        self._identity_loader = _IDENTITY_LOADER
        self._http_identity_loader = _HTTP_IDENTITY_LOADER
        self.expose = Expose(self._exposed_functions)
        self.event = Event(self._exposed_events)
        self.exception_handler = ExceptionHandler(self._exception_handlers)
        self.error = Error(self._errors)
        self.identity_loader = IdentityLoader(lambda f: setattr(self, '_identity_loader', f))
        self.http_identity_loader = IdentityLoader(lambda f: setattr(self, '_http_identity_loader', f))

    @classmethod
    def from_app(cls, app, path="/_ephaptic", redis_url=None, debug=None, allowed_origins=None, ip_header=None):
        # `app` could be ~~Flask~~ Quart, FastAPI, etc.
        instance = cls()

        instance._app = app
        instance.debug = getattr(app, 'debug', False) if debug is None else debug
        instance.allowed_origins = allowed_origins
        instance.ip_header = ip_header

        if redis_url:
            instance.manager.init_redis(redis_url)

        module = app.__class__.__module__.split(".")[0]

        match module:
            case "quart":
                from .ext.quart.adapter import QuartAdapter
                adapter = QuartAdapter(instance, app, path, instance.manager)
            case "fastapi":
                from .ext.fastapi.adapter import FastAPIAdapter
                adapter = FastAPIAdapter(instance, app, path, instance.manager)
            case _:
                raise TypeError(f"Unsupported app type: {module}")
            
        instance._exposed_functions = _EXPOSED_FUNCTIONS.copy()
        instance._exposed_events = _EXPOSED_EVENTS.copy()
        instance._exception_handlers = _EXCEPTION_HANDLERS.copy()
        instance._errors = _ERRORS.copy()
        instance._identity_loader = _IDENTITY_LOADER
        instance._http_identity_loader = _HTTP_IDENTITY_LOADER

        instance.expose = Expose(instance._exposed_functions)
        instance.event = Event(instance._exposed_events)
        instance.exception_handler = ExceptionHandler(instance._exception_handlers)
        instance.error = Error(instance._errors)
        instance.identity_loader = IdentityLoader(lambda f: setattr(instance, '_identity_loader', f))
        instance.http_identity_loader = IdentityLoader(lambda f: setattr(instance, '_http_identity_loader', f))

        return instance

    def expose_all(self, routes: Dict[str, Callable], **options):
        for name, func in routes.items():
            self.expose(name=name, **options)(func)
        return routes

    def listen(self, host: str = '127.0.0.1', port: int = 8000, path: str = '/_ephaptic'):
        """
        Serve over WebSocket without a host framework

            asyncio.run(ephaptic.listen(port=8000))
        """
        import websockets
        from .transports.websocket import StandaloneWebSocketTransport

        async def _handler(connection):
            request_path = getattr(getattr(connection, 'request', None), 'path', path)
            if request_path and request_path.split('?')[0] != path:
                await connection.close(code=1008, reason='Unknown path')
                return

            if self.allowed_origins is not None:
                headers = getattr(getattr(connection, 'request', None), 'headers', {}) or {}
                origin = headers.get('origin') if hasattr(headers, 'get') else None
                if origin not in self.allowed_origins:
                    await connection.close(code=1008, reason='Origin not allowed')
                    return

            transport = StandaloneWebSocketTransport(connection, ip_header=self.ip_header)
            await self.handle_transport(transport)

        async def _serve():
            async with websockets.serve(_handler, host, port):
                if self.manager.redis:
                    asyncio.create_task(self.manager.start_redis())
                await asyncio.Future() # run forever

        return _serve()

    def router(self, *args, **kwargs):
        from .ext.fastapi import Router
        return Router(self, *args, **kwargs)

    def include(self, router, **kwargs):
        app = getattr(self, '_app', None)
        if app is None:
            raise RuntimeError("Ephaptic isn't attached to an app. Create it with `Ephaptic.from_app(app)` first.")
        if not hasattr(app, 'include_router'):
            raise RuntimeError("ephaptic.include(...) requires a FastAPI app; the Router is FastAPI-only.")

        if hasattr(router, 'bind'):
            router.bind(self)
        app.include_router(router, **kwargs)

    async def _check_ratelimit(self, func_name: str, limit: tuple[int, int], uid: str = None, ip: str = None):
        max_reqs, window = limit
        identifier = f'u:{identity_key(uid)}' if uid is not None else f'ip:{ip}'
        now = time.time()
        current_window = int(now // window)
        reset = (current_window + 1) * window
        key = f'ephaptic:rl:{func_name}:{identifier}:{current_window}'

        if self.manager.redis:
            pipe = self.manager.redis.pipeline()
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
        if len(args) == 1 and isinstance(args[0], list): targets = list(args[0])
        else: targets = list(args)
        return EphapticTarget([identity_key(t) for t in targets], self.manager)
       
    async def emit(self, event_instance: pydantic.BaseModel):
        event_name = event_instance.__class__.__name__
        payload = event_instance.model_dump(mode='python')
        transport: Transport = _active_transport_ctx.get()
        if not transport:
            raise RuntimeError(
                f".emit({event_name}) called outside RPC context. "
                f"Use .to(...).emit({event_name}) to broadcast from background tasks, to specific user(s)."
            )
        
        # NOTE: There is slight duplication here and in the EphapticTarget. Perhaps make these functions internally route to EphapticTargets but pass the transport to use?
        
        await transport.send(encoding.encode({
            'type': 'event',
            'name': event_name,
            'payload': {'args': [], 'kwargs': payload}
        }))

    ## Error handling

    def _find_exception_handler(self, exc_type: type) -> Optional[Callable]:
        for klass in exc_type.__mro__:
            if klass in self._exception_handlers:
                return self._exception_handlers[klass]
        return None

    def _response_to_wire(self, response) -> dict:
        status = getattr(response, 'status_code', 500)
        body = getattr(response, 'body', None)
        data = None
        message = 'Error'
        if body is not None:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    if 'code' in data and 'message' in data:
                        return {'code': data['code'], 'message': data['message'], 'data': data.get('data')}
                    message = str(data.get('detail') or data.get('message') or message)
            except Exception:
                try: message = body.decode() if isinstance(body, (bytes, bytearray)) else str(body)
                except Exception: ...
        return {'code': f'HTTP_{status}', 'message': message, 'data': data}

    def _normalize_handler_result(self, result) -> dict:
        if isinstance(result, ServiceError): return result.to_wire()
        if hasattr(result, 'status_code') and hasattr(result, 'body'): return self._response_to_wire(result)
        if isinstance(result, dict) and 'code' in result: return {'code': result.get('code'), 'message': result.get('message', ''), 'data': result.get('data')}
        if isinstance(result, str): return {'code': 'ERROR', 'message': result, 'data': None}
        return {'code': 'INTERNAL', 'message': 'Internal server error.', 'data': None}

    async def _call_app_exception_handler(self, exc: Exception) -> Optional[dict]:
        app = getattr(self, '_app', None)
        handlers = getattr(app, 'exception_handlers', None)
        if not handlers: return None

        handler = None
        for klass in type(exc).__mro__:
            if klass in handlers: # klass or class_ ... an age-long debate.
                handler = handlers[klass]
                break
        if handler is None: return None

        try:
            # [TODO] It might be better to pass a Request object from Starlette if it exposes one for WebSocket requests?
            # They still have things like cookies, etc. that the developer might wish to use.
            result = handler(None, exc)
            if inspect.isawaitable(result): result = await result
        except Exception:
            return None

        return self._normalize_handler_result(result)

    async def _resolve_error(self, exc: Exception) -> dict:
        if not isinstance(exc, ServiceError):
            logger.exception("Unhandled exception in an ephaptic handler", exc_info=exc)

        if isinstance(exc, ServiceError):
            return exc.to_wire()

        handler = self._find_exception_handler(type(exc))
        if handler is not None:
            try:
                result = await self._async(handler)(exc)
            except Exception:
                logger.exception("An ephaptic exception handler itself raised")
            else:
                return self._normalize_handler_result(result)

        try:
            from starlette.exceptions import HTTPException as StarletteHTTPException
        except Exception:
            StarletteHTTPException = None
        if StarletteHTTPException is not None and isinstance(exc, StarletteHTTPException):
            return {
                'code': f'HTTP_{exc.status_code}',
                'message': str(exc.detail),
                'data': {'status_code': exc.status_code},
            }

        app_result = await self._call_app_exception_handler(exc)
        if app_result is not None:
            return app_result

        if self.debug:
            import traceback
            return {
                'code': 'INTERNAL',
                'message': f'{type(exc).__name__}: {exc}',
                'data': {'traceback': traceback.format_exc()},
            }
        return {'code': 'INTERNAL', 'message': 'Internal server error.', 'data': None}

    async def _finalise_generator(self, gen, is_async_gen: bool):
        try:
            if is_async_gen:
                await gen.aclose()
            else:
                await asyncio.to_thread(gen.close)
        except Exception:
            logger.debug("A generator raised while being finalised", exc_info=True)

    async def _encode_error(self, transport: Transport, call_id, wire: dict):
        try:
            await transport.send(encoding.encode({"id": call_id, "error": wire}))
        except (Transport.ConnectionClosed, Exception): ...

    async def _send_error(self, transport: Transport, call_id, exc: Exception):
        wire = await self._resolve_error(exc)
        await self._encode_error(transport, call_id, wire)

    ## ----
    
    async def handle_transport(self, transport: Transport):
        current_uid = None
        registry_key = None
        tasks: "set[asyncio.Task]" = set()

        def dispatch(data: dict):
            if data.get('type') == 'rpc':
                task = asyncio.create_task(self._handle_rpc(transport, data, current_uid))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

        try:
            raw = await transport.receive()
            init = encoding.decode(raw)

            if init.get('type') == 'init':
                loaded_uid = None
                try:
                    if self._identity_loader:
                        loaded_uid = await self._async(self._identity_loader)(init.get('auth'))
                except Exception:
                    logger.exception("The identity loader raised; treating the connection as anonymous")
                    loaded_uid = None

                if loaded_uid is not None:
                    try:
                        registry_key = identity_key(loaded_uid)
                    except UnsupportedIdentityError:
                        logger.exception(
                            "The identity loader returned a value that cannot be used as a registry key; "
                            "treating the connection as anonymous. Return a string, an integer, a boolean, or a UUID."
                        )
                        registry_key = None
                    else:
                        current_uid = loaded_uid
                        _active_user_ctx.set(current_uid)
                        self.manager.add(registry_key, transport)
            else:
                # if no init frame is sent, treat the connection as anonymous and continue to parse the message
                dispatch(init)

            while True:
                raw = await transport.receive()
                data = encoding.decode(raw)
                dispatch(data)
        except (asyncio.CancelledError, Transport.ConnectionClosed): ...
        except Exception:
            logger.exception("An ephaptic connection failed")
        finally:
            for task in tasks: task.cancel()
            if registry_key is not None: self.manager.remove(registry_key, transport)

    async def _handle_rpc(self, transport: Transport, data: dict, current_uid):
        call_id = data.get('id')
        func_name = data.get('name')
        args = data.get('args', [])
        kwargs = data.get('kwargs', {}) # [INFO] Only Python client (currently) sends these, JS client does not.

        try:
            if func_name not in self._exposed_functions:
                await self._encode_error(transport, call_id, {
                    "code": "NOT_FOUND",
                    "message": f"Function '{func_name}' not found.",
                    "data": None,
                })
                return

            target_func = self._exposed_functions[func_name]
            meta = getattr(target_func, META_KEY, {})

            if meta.get('requires_login') and current_uid is None:
                await self._encode_error(transport, call_id, {
                    "code": "UNAUTHORIZED",
                    "message": "Unauthorized.",
                    "data": None,
                })
                return

            if meta.get('rate_limit'):
                # this function raises the RatelimitExceeded exception for us, dw
                await self._check_ratelimit(
                    func_name,
                    meta.get('rate_limit'),
                    uid=current_uid,
                    ip=transport.remote_addr,
                )

            hints = meta.get('hints') or typing.get_type_hints(target_func)
            sig = meta.get('sig') or inspect.signature(target_func)

            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
            except TypeError as e:
                await self._encode_error(transport, call_id, {
                    "code": "VALIDATION_ERROR",
                    "message": str(e),
                    "data": None,
                })
                return

            variadic = {
                name for name, param in sig.parameters.items()
                if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            }

            fields = {}
            for name, param in sig.parameters.items():
                if name in variadic: continue
                if name in hints:  fields[name] = (hints[name], param.default if param.default is not inspect.Parameter.empty else ...)
                else:              fields[name] = (Any, param.default if param.default is not inspect.Parameter.empty else ...)

            DynamicInputModel = pydantic.create_model(f'DynamicInputModel_{func_name}', **fields)

            try:
                validated_data = DynamicInputModel(
                    **{k: v for k, v in bound.arguments.items() if k not in variadic}
                )
                final_arguments = {
                    field_name: getattr(validated_data, field_name)
                    for field_name in DynamicInputModel.model_fields.keys()
                }
                has_var_positional = any(
                    param.kind is inspect.Parameter.VAR_POSITIONAL
                    for param in sig.parameters.values()
                )
                call_args: list = []
                call_kwargs: dict = {}
                for name, param in sig.parameters.items():
                    kind = param.kind
                    if kind is inspect.Parameter.VAR_POSITIONAL: call_args.extend(bound.arguments.get(name, ()))
                    elif kind is inspect.Parameter.VAR_KEYWORD: call_kwargs.update(bound.arguments.get(name, {}))
                    elif name not in final_arguments: continue
                    elif kind is inspect.Parameter.POSITIONAL_ONLY or (kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and has_var_positional): call_args.append(final_arguments[name])
                    else: call_kwargs[name] = final_arguments[name]
            except pydantic.ValidationError as e:
                await self._encode_error(transport, call_id, {
                    "code": "VALIDATION_ERROR",
                    "message": "Input validation failed.",
                    "data": json.loads(e.json(include_url=False)),
                })
                return

            token_transport = _active_transport_ctx.set(transport)
            token_user = _active_user_ctx.set(current_uid)
            token_scope = _scope_ctx.set('rpc')

            def validate(payload, expected, adapter=None):
                if expected and expected is not inspect.Signature.empty and expected is not typing.Any:
                    adapter = adapter or pydantic.TypeAdapter(expected)
                    validated = adapter.validate_python(payload, from_attributes=True)
                    return adapter.dump_python(validated, mode='python')
                elif isinstance(payload, pydantic.BaseModel):
                    # incase dev returned basemodel and forgot to set return type
                    return payload.model_dump(mode='python')
                else: return payload

            try:
                result = await self._async(target_func)(*call_args, **call_kwargs)

                is_async_gen = inspect.isasyncgen(result)
                is_sync_gen = inspect.isgenerator(result)

                return_type = meta.get('response_model') or hints.get("return", typing.Any)

                if is_async_gen or is_sync_gen:
                    origin = typing.get_origin(return_type)
                    origin_name = getattr(origin, '__name__', '')
                    if origin in (typing.AsyncGenerator, typing.Generator, typing.AsyncIterable, typing.Iterable) or origin_name in ('AsyncGenerator', 'Generator', 'AsyncIterable', 'Iterable'):
                        type_ = typing.get_args(return_type)[0] if typing.get_args(return_type) else typing.Any
                    else: type_ = return_type

                    if type_ and type_ is not inspect.Signature.empty and type_ is not typing.Any:
                        adapter = pydantic.TypeAdapter(type_)
                    else: adapter = None

                    try:
                        await transport.send(encoding.encode({
                            'id': call_id,
                            'stream': True,
                        }))

                        if is_async_gen:
                            async for chunk in result:
                                chunk_data = validate(chunk, type_)
                                await transport.send(encoding.encode({
                                    'id': call_id,
                                    'chunk': chunk_data,
                                }))
                        else:
                            # can't use `await to_thread(next, gen)` directly because
                            # coroutines use StopIteration internally to signal
                            # completion and Python raises if it escapes an await.
                            def next_(gen):
                                try:
                                    return next(gen), False
                                except StopIteration:
                                    return None, True

                            while True:
                                chunk, done = await asyncio.to_thread(next_, result)
                                if done: break
                                chunk_data = validate(chunk, type_)
                                await transport.send(encoding.encode({
                                    'id': call_id,
                                    'chunk': chunk_data,
                                }))

                        await transport.send(encoding.encode({
                            'id': call_id,
                            'done': True,
                        }))
                        return

                    except (Transport.ConnectionClosed, asyncio.CancelledError):
                        await self._finalise_generator(result, is_async_gen)
                        raise

                    except Exception as e:
                        await self._finalise_generator(result, is_async_gen)
                        await self._send_error(transport, call_id, e)
                        return

                elif return_type and return_type is not inspect.Signature.empty and return_type is not typing.Any:
                    try:
                        adapter = pydantic.TypeAdapter(return_type)
                        validated = adapter.validate_python(result, from_attributes=True)
                        result = adapter.dump_python(validated, mode='python')
                    except Exception as e:
                        # Should we really treat this separately?
                        # For input it's understandable, but for server responses it feels like a server issue.
                        # Let's just return a RETURN_VALIDATION_ERROR and print the traceback.
                        import traceback
                        traceback.print_exc()
                        await self._encode_error(transport, call_id, {
                            "code": "RETURN_VALIDATION_ERROR",
                            "message": f"Server returned invalid type: {e}" if self.debug else "Server returned an invalid type.",
                            "data": None,
                        })
                        return
                elif isinstance(result, pydantic.BaseModel):
                    result = result.model_dump(mode='python')

                await transport.send(encoding.encode({"id": call_id, "result": result}))
            finally:
                _active_transport_ctx.reset(token_transport)
                _active_user_ctx.reset(token_user)
                _scope_ctx.reset(token_scope)
        except (asyncio.CancelledError, Transport.ConnectionClosed): ...
        except Exception as e: await self._send_error(transport, call_id, e)