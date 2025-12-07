from . import Transport

class WebSocketTransport(Transport):
    def __init__(self, ws):
        self.ws = ws

    async def send(self, data: bytes):
        await self.ws.send(data)

    async def receive(self) -> bytes:
        return await self.ws.receive()