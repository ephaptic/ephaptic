from quart import websocket, Quart
from ..transports.websocket import WebSocketTransport

class QuartAdapter:
    def __init__(self, ephaptic, app: Quart, path, manager):
        self.ephaptic = ephaptic

        @app.websocket(path)
        async def ephaptic_ws():
            transport = WebSocketTransport(websocket)
            await self.ephaptic.handle_transport(transport)

        if manager.redis:
            @app.before_serving
            async def start_redis():
                app.add_background_task(manager.start_redis)