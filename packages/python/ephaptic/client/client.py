import asyncio
import msgpack
import websockets
import logging

from typing import Callable, Any, Optional
import inspect

class EphapticClient:
    def __init__(self, url: str, auth = None):
        self.url = url
        self.auth = auth
        self.ws = None
        self._call_id = 0
        self._pending_calls = {} # id -> asyncio.Future (asyncio.Future is a Python equivalent of a Promise)
        self._event_handlers = {} # name: str -> Set(Callable)
        self._listen_task = None

    def _async(self, func: Callable):
        async def wrapper(*args, **kwargs) -> Any:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        return wrapper

    async def connect(self):
        if self.ws: return

        self.ws = await websockets.connect(self.url)

        payload = {"type": "init"}
        if self.auth: payload["auth"] = self.auth

        await self.ws.send(msgpack.dumps(payload))

        self._listen_task = asyncio.create_task(self._listener())

    async def _listener(self):
        try:
            async for message in self.ws:
                data = msgpack.loads(message)

                if data.get('id') is not None:
                    call_id = data['id']
                    if call_id in self._pending_calls:
                        future = self._pending_calls.pop(call_id)
                        if 'error' in data:
                            future.set_exception(Exception(data['error']))
                        else:
                            future.set_result(data.get('result'))
                
                elif data.get('type') == 'event':
                    name = data['name']
                    payload = data.get('payload', {})
                    args = payload.get('args', [])
                    kwargs = payload.get('kwargs', {})

                    if name in self._event_handlers:
                        for handler in self._event_handlers[name]:
                            try:
                                asyncio.create_task(self._async(handler)(*args, **kwargs))
                                # We don't await it, we want to execute all handlers in parallel.
                            except Exception as e:
                                logging.error(f"Error in event handler {name}: {e}")
        
        except Exception as e:
            logging.error(f"Connection error: {e}")

    def on(self, event_name, func: Optional[Callable] = None):
        def decorator(f):
            if event_name not in self._event_handlers: self._event_handlers[event_name] = set()
            self._event_handlers[event_name].add(func)
            return f
        
        return decorator(func) if func else decorator

    def off(self, event_name, func: Callable):
        if event_name not in self._event_handlers: return
        s = self._event_handlers[event_name]
        s.discard(func)
        if not s: del self._event_handlers[event_name]

    def once(self, event_name, func: Optional[Callable] = None):
        def decorator(f):
            async def wrapper(*args, **kwargs):
                self.off(event_name, wrapper)
                func(*args, **kwargs)
            self.on(event_name, wrapper)
            return f
        
        return decorator(func) if func else decorator

    def __getattr__(self, name):
        async def remote_call(*args, **kwargs):
            if not self.ws: await self.connect()

            self._call_id += 1
            call_id = self._call_id

            future = asyncio.Future()
            self._pending_calls[call_id] = future

            payload = {
                "type": "rpc",
                "id": call_id,
                "name": name,
                "args": args,
                "kwargs": kwargs,
            }

            await self.ws.send(msgpack.dumps(payload))
            return await future
        
        return remote_call



async def connect(url: str = "ws://localhost:8000/_ephaptic", auth = None):
    client = EphapticClient(url, auth)
    await client.connect()
    return client