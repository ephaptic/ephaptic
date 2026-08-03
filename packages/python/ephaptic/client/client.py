import asyncio
import weakref
import msgpack
import websockets
import logging
import random
import re

from typing import Callable, Any, Optional
import inspect

from .queue import AsyncQueue
from ..errors import EphapticError

logger = logging.getLogger('ephaptic.client')

# Connection states, per SPEC-CLIENT 5.2.
DISCONNECTED = 'disconnected'
CONNECTING = 'connecting'
CONNECTED = 'connected'
RECONNECTING = 'reconnecting'
CLOSED = 'closed'

_ABSENT = object()


class EphapticClient:
    def __init__(self, url: str, auth = _ABSENT, timeout: Optional[float] = 30.0):
        self.url = _normalise_url(url)
        self.auth = auth
        self.timeout = timeout
        self.ws = None
        self._call_id = 0
        self._pending_calls: dict[int, asyncio.Future] = {}
        # Held weakly. A strong reference here would keep the backing async
        # generator alive forever, so a consumer that abandons its loop would
        # never release the registration and the buffer would grow without
        # bound (CLT-STR-008).
        self._pending_streams: 'weakref.WeakValueDictionary[int, AsyncQueue]' = weakref.WeakValueDictionary()
        self._event_handlers: dict[str, set[callable]] = {}
        self._once_wrappers: dict[tuple, Callable] = {}
        # Reverse index, so a wrapper can be retired at dispatch without
        # searching every registration.
        self._once_by_wrapper: dict[Callable, tuple] = {}
        self._listen_task = None
        self._reconnect_task = None
        self._closed = False
        self._retry_count = 0
        self._state = DISCONNECTED
        self._state_listeners: set[Callable] = set()
        # Serialises connects so only one transport connection exists at a time
        # (CLT-CONN-009).
        self._connect_lock = asyncio.Lock()

    # --- Connection state ---------------------------------------------------

    @property
    def state(self) -> str:
        '''The current connection state (CLT-CONN-007).'''
        return self._state

    def on_state_change(self, callback: Callable[[str], None]):
        '''Observe connection state transitions. Returns the callback so it may be
        passed to `off_state_change`.'''
        self._state_listeners.add(callback)
        return callback

    def off_state_change(self, callback: Callable[[str], None]):
        self._state_listeners.discard(callback)

    def _set_state(self, state: str):
        if self._state == state: return
        self._state = state
        for listener in list(self._state_listeners):
            try: listener(state)
            except Exception:
                logger.exception("A connection-state listener raised")

    def _async(self, func: Callable):
        async def wrapper(*args, **kwargs) -> Any:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        return wrapper

    # --- Lifecycle ----------------------------------------------------------

    async def connect(self):
        async with self._connect_lock:
            if self.ws: return

            self._closed = False
            self._set_state(RECONNECTING if self._retry_count else CONNECTING)

            try:
                self.ws = await websockets.connect(self.url)
            except Exception as e:
                self._set_state(DISCONNECTED)
                # Every failure the application observes is a typed error
                # (CLT-ERR-002).
                raise EphapticError('CONNECT_FAILED', f'Could not connect to {self.url}: {e}') from None

            # The first call of each connection bears identifier 1 (CLT-WIRE-010).
            self._call_id = 0

            payload = {"type": "init"}
            # Presence, not truthiness: an auth value of '' or 0 was still supplied
            # by the application (CLT-WIRE-014).
            if self.auth is not _ABSENT: payload["auth"] = self.auth

            try:
                await self.ws.send(msgpack.dumps(payload))
            except Exception as e:
                self.ws = None
                self._set_state(DISCONNECTED)
                raise EphapticError('CONNECT_FAILED', f'Could not initialise the connection: {e}') from None

            # Reset only now: 5.2 defines `connected` as the transport being open
            # *and* the init frame sent, so a failed handshake must not reset the
            # backoff (CLT-CONN-003).
            self._retry_count = 0
            self._set_state(CONNECTED)
            self._listen_task = asyncio.create_task(self._listener())

    async def disconnect(self):
        '''Close the connection, suppress reconnection, and release every resource
        the client holds (CLT-CONN-008).'''
        self._closed = True

        current = asyncio.current_task()
        for attr in ('_listen_task', '_reconnect_task'):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None and task is not current:
                task.cancel()

        ws, self.ws = self.ws, None
        if ws is not None:
            try: await ws.close()
            except Exception: ...

        # Pending work is failed even where the socket never reports a close
        # (CLT-CONN-005).
        self._fail_pending(EphapticError('DISCONNECTED', 'The client disconnected.'))
        self._set_state(CLOSED)

    # --- Receive loop -------------------------------------------------------

    async def _listener(self):
        cancelled = False
        try:
            async for message in self.ws:
                try:
                    data = msgpack.loads(message)
                except Exception:
                    # A malformed frame must not terminate the connection
                    # (CLT-WIRE-024).
                    logger.warning("Discarding an undecodable frame")
                    continue

                try:
                    self._dispatch_frame(data)
                except Exception:
                    logger.warning("Discarding a structurally invalid frame", exc_info=True)
                    self._fail_identified(data, 'The server sent a frame this client could not interpret.')

        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:
            logger.warning(f"Connection error: {e}")
        finally:
            # Pending work is failed on every path, including cancellation. Only
            # the *reconnect* is suppressed during teardown (CLT-CONN-005 vs
            # CLT-CONN-006).
            self._fail_pending(EphapticError('DISCONNECTED', 'Connection closed before a response was received.'))
            self.ws = None
            if cancelled or self._closed:
                self._set_state(CLOSED if self._closed else DISCONNECTED)
            else:
                self._reconnect_task = asyncio.create_task(self._schedule_reconnect())

    async def _deliver(self, handler, args, kwargs):
        '''Invoke one event recipient. A recipient that raises must not prevent
        delivery to the others, nor propagate (CLT-EVT-004).'''
        try:
            result = handler(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("An event recipient raised")

    def _dispatch_frame(self, data):
        if not isinstance(data, dict): return

        if data.get('type') == 'event':
            name = data.get('name')
            payload = data.get('payload') or {}
            args = payload.get('args') or []
            kwargs = payload.get('kwargs') or {}

            for handler in list(self._event_handlers.get(name, ())):
                once_key = self._once_by_wrapper.get(handler)
                if once_key is not None:
                    self._event_handlers.get(name, set()).discard(handler)
                    self._once_wrappers.pop(once_key, None)
                    self._once_by_wrapper.pop(handler, None)
                    if not self._event_handlers.get(name):
                        self._event_handlers.pop(name, None)

                try:
                    coro = self._deliver(handler, args, kwargs)
                except Exception:
                    logger.exception(f"Error dispatching event {name}")
                    continue
                asyncio.create_task(coro)
            return

        if 'id' not in data: return
        call_id = data['id']

        if 'stream' in data and data['stream']:
            future = self._pending_calls.pop(call_id, None)
            if future is None or future.done():
                return

            stream = AsyncQueue()
            stream.on_abandon = lambda: self._pending_streams.pop(call_id, None)
            self._pending_streams[call_id] = stream
            future.set_result(stream)

        elif 'chunk' in data:
            stream = self._pending_streams.get(call_id)
            if stream is not None: stream.push(data['chunk'])

        elif 'done' in data and data['done']:
            stream = self._pending_streams.pop(call_id, None)
            if stream is not None: stream.close()

        elif 'error' in data and call_id in self._pending_streams:
            self._pending_streams.pop(call_id).throw(EphapticError.from_wire(data['error']))

        elif call_id in self._pending_calls:
            future = self._pending_calls.pop(call_id)
            if future.done(): return
            if 'error' in data:
                future.set_exception(EphapticError.from_wire(data['error']))
            elif 'result' in data:
                future.set_result(data['result'])
            else:
                future.set_exception(EphapticError(
                    'PROTOCOL_ERROR',
                    "The server is confused. No, I don't know why.",
                ))

    def _fail_identified(self, data, message: str):
        if not isinstance(data, dict): return
        call_id = data.get('id')
        if call_id is None: return

        error = EphapticError('PROTOCOL_ERROR', message)
        future = self._pending_calls.pop(call_id, None)
        if future is not None and not future.done():
            future.set_exception(error)
        stream = self._pending_streams.pop(call_id, None)
        if stream is not None:
            stream.throw(error)

    def _fail_pending(self, error: EphapticError):
        for future in list(self._pending_calls.values()):
            if not future.done(): future.set_exception(error)
        self._pending_calls.clear()

        for stream in list(self._pending_streams.values()):
            stream.throw(error)
        self._pending_streams.clear()

    async def _schedule_reconnect(self):
        if self._closed: return

        # min(30, 1 * 2^attempt) + RandInt(0, 1)
        delay = min(30, 1 * (2 ** self._retry_count)) + random.random()
        self._retry_count += 1
        self._set_state(RECONNECTING)
        logger.warning(f"[ephaptic] connection lost. reconnecting in {round(delay, 1)}s...")

        try: await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._set_state(CLOSED if self._closed else DISCONNECTED)
            raise

        if self._closed:
            self._set_state(CLOSED)
            return

        try: await self.connect()
        except Exception:
            if not self._closed:
                self._reconnect_task = asyncio.create_task(self._schedule_reconnect())

    ## Events

    def on(self, event_name, func: Optional[Callable] = None):
        def decorator(f):
            self._event_handlers.setdefault(event_name, set()).add(f)
            return f

        return decorator(func) if func is not None else decorator

    def off(self, event_name, func: Callable):
        handlers = self._event_handlers.get(event_name)
        if not handlers: return
        handlers.discard(func)
        wrapper = self._once_wrappers.pop((event_name, func), None)
        if wrapper is not None:
            handlers.discard(wrapper)
            self._once_by_wrapper.pop(wrapper, None)
        if not handlers: self._event_handlers.pop(event_name, None)

    def once(self, event_name, func: Optional[Callable] = None):
        def decorator(f):
            def wrapper(*args, **kwargs):
                return f(*args, **kwargs)

            self._once_wrappers[(event_name, f)] = wrapper
            self._once_by_wrapper[wrapper] = (event_name, f)
            self._event_handlers.setdefault(event_name, set()).add(wrapper)
            return f

        return decorator(func) if func is not None else decorator

    ## Calls

    async def call(self, name: str, *args, **kwargs):
        if self._closed:
            raise EphapticError('DISCONNECTED', 'The client has been disconnected.')

        if not self.ws: await self.connect()

        self._call_id += 1
        call_id = self._call_id

        try:
            frame = msgpack.dumps({
                "type": "rpc",
                "id": call_id,
                "name": name,
                "args": args,
                "kwargs": kwargs,
            })
        except Exception as e:
            raise EphapticError('ENCODE_ERROR', f"Could not encode the arguments of '{name}': {e}") from None

        future = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = future

        try:
            await self.ws.send(frame)
        except Exception as e:
            self._pending_calls.pop(call_id, None)
            raise EphapticError('DISCONNECTED', f"The connection closed before '{name}' could be sent: {e}") from None

        if self.timeout is None:
            return await future

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending_calls.pop(call_id, None)
            raise EphapticError('TIMEOUT', f'{name} timed out; exceeded {self.timeout}s.') from None
        except asyncio.CancelledError:
            self._pending_calls.pop(call_id, None)
            raise

    def __getattr__(self, name):
        if name.startswith('_'): raise AttributeError(name)

        async def remote_call(*args, **kwargs):
            return await self.call(name, *args, **kwargs)

        return remote_call


def _normalise_url(url: str) -> str:
    if not isinstance(url, str): return url
    match = re.match(r'^(https?)://', url, re.IGNORECASE)
    if match: return ('wss://' if match.group(1).lower() == 'https' else 'ws://') + url[match.end():]
    return url


async def connect(url: str = "ws://localhost:8000/_ephaptic", auth = _ABSENT, timeout: Optional[float] = 30.0):
    client = EphapticClient(url, auth, timeout)
    await client.connect()
    return client