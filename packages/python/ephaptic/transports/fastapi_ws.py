from .websocket import WebSocketTransport
from . import Transport
from starlette.websockets import WebSocket, WebSocketDisconnect

class FastAPIWebSocketTransport(WebSocketTransport):
    def __init__(self, ws: WebSocket):
        super().__init__(ws)
        self.remote_addr = ws.client.host if ws.client else 'unknown'

    async def send(self, data: bytes):
        await self.ws.send_bytes(data)

    async def receive(self) -> bytes:
        try:
            return await self.ws.receive_bytes()
        except WebSocketDisconnect:
            raise Transport.ConnectionClosed from None
