from fastapi import FastAPI, WebSocket
from ..transports.fastapi_ws import FastAPIWebSocketTransport

class FastAPIAdapter:
    def __init__(self, ephaptic, app: FastAPI, path, manager):
        self.ephaptic = ephaptic

        @app.websocket(path)
        async def ephaptic_ws(websocket: WebSocket):
            await websocket.accept()
            transport = FastAPIWebSocketTransport(websocket)
            await self.ephaptic.handle_transport(transport)

        if manager.redis:
            lifespan = app.router.lifespan_context

            from contextlib import asynccontextmanager
            import asyncio

            @asynccontextmanager
            async def ephaptic_lifespan_wrapper(app):
                asyncio.create_task(manager.start_redis())

                if lifespan:
                    async with lifespan(app) as state:
                        yield state
                else:
                    yield

            app.router.lifespan_context = ephaptic_lifespan_wrapper
