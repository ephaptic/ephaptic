from .websocket import WebSocketTransport
from . import Transport
from starlette.websockets import WebSocket, WebSocketDisconnect

class FastAPIWebSocketTransport(WebSocketTransport):
    def __init__(self, ws: WebSocket, ip_header: str = None):
        super().__init__(ws)
        self.remote_addr = ws.client.host if ws.client else 'unknown'

        if ip_header:
            forwarded = ws.headers.get(ip_header)
            if forwarded:
                self.remote_addr = forwarded.split(',')[0].strip()

    async def send(self, data: bytes):
        try:
            async with self._send_lock:
                await self.ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            raise Transport.ConnectionClosed from None

    async def receive(self) -> bytes:
        try:
            return await self.ws.receive_bytes()
        except (WebSocketDisconnect, RuntimeError):
            raise Transport.ConnectionClosed from None
