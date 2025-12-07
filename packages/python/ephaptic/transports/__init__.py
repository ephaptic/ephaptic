from typing import Coroutine

class Transport:
    async def send(data: bytes): raise NotImplementedError()
    async def receive() -> bytes: raise NotImplementedError()