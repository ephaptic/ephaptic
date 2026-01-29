from typing import Optional

class Transport:
    remote_addr: Optional[str] = None # usually, IP address (for most common transport types, like websocket, tcp/udp, etc.)

    class ConnectionClosed(Exception):
        pass

    async def send(self, data: bytes): raise NotImplementedError()
    async def receive(self) -> bytes: raise NotImplementedError()