from . import Transport

class WebSocketTransport(Transport):
    def __init__(self, ws, ip_header: str = None):
        self.ws = ws
        self.remote_addr = getattr(ws, 'remote_addr', None)

        if ip_header:
            headers = getattr(ws, 'headers', None)
            forwarded = headers.get(ip_header) if headers is not None and hasattr(headers, 'get') else None
            if forwarded: self.remote_addr = forwarded.split(',')[0].strip()

    async def send(self, data: bytes):
        try:
            async with self._send_lock: await self.ws.send(data)
        except Transport.ConnectionClosed: raise
        except Exception as e:
            if isinstance(e, (RuntimeError, ConnectionError)): raise Transport.ConnectionClosed from None
            raise

    async def receive(self) -> bytes:
        return await self.ws.receive()

class StandaloneWebSocketTransport(Transport):

    def __init__(self, connection, ip_header: str = None):
        self.connection = connection

        remote = getattr(connection, 'remote_address', None)
        self.remote_addr = remote[0] if isinstance(remote, (tuple, list)) and remote else None

        if ip_header:
            headers = getattr(getattr(connection, 'request', None), 'headers', None)
            forwarded = headers.get(ip_header) if headers is not None and hasattr(headers, 'get') else None
            if forwarded:
                self.remote_addr = forwarded.split(',')[0].strip()

    async def send(self, data: bytes):
        import websockets
        try:
            async with self._send_lock:
                await self.connection.send(data)
        except websockets.exceptions.ConnectionClosed:
            raise Transport.ConnectionClosed from None

    async def receive(self) -> bytes:
        import websockets
        try:
            return await self.connection.recv()
        except websockets.exceptions.ConnectionClosed:
            raise Transport.ConnectionClosed from None