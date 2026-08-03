from quart import websocket, Quart
from ...transports.websocket import WebSocketTransport

class QuartAdapter:
    def __init__(self, ephaptic, app: Quart, path, manager):
        self.ephaptic = ephaptic

        @app.websocket(path)
        async def ephaptic_ws():
            if ephaptic.allowed_origins is not None:
                origin = websocket.headers.get('Origin')
                if origin not in ephaptic.allowed_origins:
                    return '', 403

            # `quart.websocket` returns any currently in use websocket but we need OUR websocket
            connection = websocket._get_current_object()

            transport = WebSocketTransport(connection, ip_header=ephaptic.ip_header)
            await self.ephaptic.handle_transport(transport)

        if manager.redis:
            @app.before_serving # while reviewing this code i accidentally read this as `@app.before_starving` 😭
            async def start_redis():
                app.add_background_task(manager.start_redis)