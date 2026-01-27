from typing import Coroutine

class Transport:
    class ConnectionClosed(Exception):
        pass

    async def send(self, data: bytes): raise NotImplementedError()
    async def receive(self) -> bytes: raise NotImplementedError()