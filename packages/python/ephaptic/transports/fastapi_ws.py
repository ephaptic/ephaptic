from .websocket import WebSocketTransport
from . import Transport
from starlette.websockets import WebSocketDisconnect

class FastAPIWebSocketTransport(WebSocketTransport):
    async def send(self, data: bytes):
        await self.ws.send_bytes(data)

    async def receive(self) -> bytes:
        try:
            return await self.ws.receive_bytes()
        except WebSocketDisconnect:
            raise Transport.ConnectionClosed from None
