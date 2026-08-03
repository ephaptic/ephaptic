# The Router

The Router is a FastAPI-specific way to expose your API routes both to an Ephaptic client, and to HTTP clients.

The Router is the recommended way that you should build a fullstack app with ephaptic if you are planning on having many different clients.

There are also multiple ways to instantiate it.

### 1. Top-level import

```python
from ephaptic import Router
router = Router(ephaptic)
```

### 2. Extended import (you'd never use this)

```python
from ephaptic.ext.fastapi import Router
router = Router(ephaptic)
```

### 3. Factory from your instance

```python
router = ephaptic.router()
```

### 4. Create the Router before Ephaptic is instantiated

```python title="services/users.py"
from ephaptic import Router

router = Router(prefix="/users")
```

```python title="app.py"
from .services.users import router

ephaptic.include(router)
```

## Features

It comes with the following benefits:

- You can mount it with `ephaptic.include(router)` - this binds the Router to your instance *and* includes it on the app (extra kwargs like `prefix`/`tags` are forwarded to FastAPI's `include_router`). A plain `app.include_router(router)` also works if the Router is already bound to Ephaptic.
- Since both FastAPI and Ephaptic share the same Pydantic validation strategy, you can type-hint the function arguments and response model and both FastAPI and Ephaptic will handle it properly.
    - Ephaptic will return your Pydantic model or primitive type or a combination of both as a TypeScript interface on the client (or whatever other Ephaptic client you use), while FastAPI will return it JSON-serialized for your other clients.
- Functions exposed via the Router will show up in the FastAPI-generated `openapi.json`, meaning Ephaptic routes will even show up, fully typed, in your Swagger UI.
- You only need to define your identity loader (ephaptic) and your http identity loader (you are passed a `fastapi.Request` object as context) once, then they are both selectively used and stored as the `active_user()`.
- For specific logic, you can use `ephaptic.ctx.is_http()` and `ephaptic.ctx.is_rpc()` within your functions. Instead of defining two almost duplicated functions for RPC-specific and HTTP-specific logic, you can put them under one function and then use these in an if-statement to branch out your logic.
- There are built in Quality-Of-Life features like a ratelimiter that works both with FastAPI and Ephaptic.
- You can even write streaming logic in both RPC and HTTP!
    - Ephaptic uses the simple `for await (const x of stream) { ... }` syntax, while for HTTP it returns your objects in a JSONL SSE format. Works with OpenAPI also.
    - For both formats you simply have to annotate response with `AsyncGenerator` / `Generator`, and `yield` each item.
- Typed, structured errors work in both worlds: raise a `ServiceError` (or a FastAPI `HTTPException`) and it becomes a proper HTTP response for HTTP clients, and a typed `EphapticError` for RPC clients. See [Error Handling](errors.md).

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
ephaptic = Ephaptic.from_app(app)

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

ephaptic.include(router)
```

Now, you can run this app, and send both authenticated and unauthenticated requests via a HTTP client and an Ephaptic client, and verify they work as intended. You can even go to `/docs` and see the echo function there!

!!! tip
    This works even if you defined the Router in a separate file without the `Ephaptic` instance. Because `ephaptic.include(...)` binds it for you:
    
    ```python
    router = Router() # you don't need any arguments

    ephaptic.include(router) # this gives the router a handle onto Ephaptic, *and* it includes the router to the FastAPI app.
    ```
