from .websocket import WebSocketTransport

class FastAPIWebSocketTransport(WebSocketTransport):
    async def send(self, data: bytes):
        await self.ws.send_bytes(data)

    async def receive(self) -> bytes:
        return await self.ws.receive_bytes()
