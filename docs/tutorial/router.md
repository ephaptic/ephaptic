# The Router

The Router is a FastAPI-specific way to expose your API routes both to an Ephaptic client, and to HTTP clients.

## Features

It comes with the following benefits:

- You can `app.include_router(router)` with an Ephaptic Router the same way you can with a FastAPI APIRouter.
- Since both FastAPI and Ephaptic share the same Pydantic validation strategy, you can type-hint the function arguments and response model and both FastAPI and Ephaptic will handle it properly.
    - Ephaptic will return your Pydantic model or primitive type or a combination of both as a TypeScript interface on the client (or whatever other Ephaptic client you use), while FastAPI will return it JSON-serialized for your other clients.
- Functions exposed via the Router will show up in the FastAPI-generated `openapi.json`, meaning Ephaptic routes will even show up, fully typed, in your Swagger UI.
- You only need to define your identity loader (ephaptic) and your http identity loader (you are passed a `fastapi.Request` object as context) once, then they are both selectively used and stored as the `active_user()`.
- For specific logic, you can use `ephaptic.ctx.is_http()` and `ephaptic.ctx.is_rpc()` within your functions. Instead of defining two almost duplicated functions for RPC-specific and HTTP-specific logic, you can put them under one function and then use these in an if-statement to branch out your logic.

But how do you use it?

## Usage

### HTTP Identity Loader

!!! info
    The `http_identity_loader` is just like the other decorators. You can call it from the global import (`from ephaptic import http_identity_loader`) or you can use `@ephaptic.http_identity_loader` where `ephaptic` is your Ephaptic instance.

```python
from fastapi import FastAPI, Request
from ephaptic import Ephaptic, active_user
from ephaptic.ctx import is_http, is_rpc
from ephaptic.ext.fastapi import Router
import pydantic

app = FastAPI()
ephaptic = ephaptic.from_app(app)

@ephaptic.identity_loader
def load_user(auth: str):
    return auth # Obviously, in real life, you'd use a real authentication solution, like JWTs.

@ephaptic.http_identity_loader
def load_user_http(request: Request):
    auth = request.headers.get('Authorization')
    if not auth: return None
    return auth.removeprefix('Bearer ')

router = Router(ephaptic)

class EchoResult(pydantic.BaseModel):
    is_rpc: bool
    is_http: bool
    active_user: str
    message: str

@router.get('/echo', requires_login=True) # requires_login means the result of load_user must NOT be None.
def echo(message: str) -> EchoResult:
    return EchoResult(
        is_rpc=is_rpc(),
        is_http=is_http(),
        active_user=active_user(),
        message=message,
    )

app.include_router(router)
```

Now, you can run this app, and send both authenticated and unauthenticated requests via a HTTP client and an Ephaptic client, and verify they work as intended. You can even go to `/docs` and see the echo function there!