from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from ...transports.fastapi_ws import FastAPIWebSocketTransport
from ...errors import ServiceError
from .middleware import CtxMiddleware

class FastAPIAdapter:
    def __init__(self, ephaptic, app: FastAPI, path, manager):
        self.ephaptic = ephaptic

        app.add_middleware(CtxMiddleware, ephaptic=ephaptic)

        @app.exception_handler(ServiceError)
        async def _service_error_handler(request, exc: ServiceError):
            headers = {}
            if isinstance(exc.data, dict) and 'retry_after' in exc.data:
                headers['Retry-After'] = str(exc.data['retry_after'])
            return JSONResponse(status_code=exc.status_code, content=exc.to_wire(), headers=headers)

        @app.websocket(path)
        async def ephaptic_ws(websocket: WebSocket):
            if ephaptic.allowed_origins is not None:
                origin = websocket.headers.get('origin')
                if origin not in ephaptic.allowed_origins:
                    await websocket.close(code=1008) # policy violation
                    return

            await websocket.accept()
            transport = FastAPIWebSocketTransport(websocket, ip_header=ephaptic.ip_header)
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