import asyncio
from typing import Optional

class Transport:
    remote_addr: Optional[str] = None # usually, IP address (for most common transport types, like websocket, tcp/udp, etc.)

    class ConnectionClosed(Exception):
        pass

    @property
    def _send_lock(self) -> asyncio.Lock:
        lock = self.__dict__.get('_send_lock_obj')
        if lock is None:
            lock = asyncio.Lock()
            self.__dict__['_send_lock_obj'] = lock
        return lock

    async def send(self, data: bytes): raise NotImplementedError()
    async def receive(self) -> bytes: raise NotImplementedError()