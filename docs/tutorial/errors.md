# Handling errors

Things are guaranteed to go wrong at some point in your application. If you tell me your application has never raised an error 500 before, you are either lying or ignorant. Sorry.

But Ephaptic gives you a structured way to throw and catch these errors across a language boundary, while keeping the format that you are already used to.

## Raising Errors

This is how you would raise a typed error on the server:

```python
from ephaptic import ServiceError

@ephaptic.expose
async def get_item(id: int) -> Item:
    item = await db.find(id)
    if not item:
        raise ServiceError('That item does not exist.', code='NOT_FOUND', data={ 'id': id })
    return item
```

The `data` in the error can be any serializable value, but if you want it to be typed, you can register it (see [typed data](##typed-data)) and then the type of the `data` will be in the schema and the client's types.

In this case, the client will catch the `EphapticError`, and on this error the attribute `code` will be `'NOT_FOUND'`, the `message` will be `'That item does not exist.'`, and the `data` will be `{ id }`.

## Catching Errors

When an exposed function raises an error, the client promise rejects with the error. You can catch the `EphapticError`, which has three properties:

- `code` - a short string describing the error (e.g. `NOT_FOUND`).
- `message` - a friendly description of the error (e.g. `This item is not on our server!`).
- `data` - an optional payload, with extra (typed!) information that you can populate when raising it.

=== "TypeScript"

    ```typescript
    import { EphapticError } from '@ephaptic/client';

    try {
        await client.withdraw(500);
    } catch (err) {
        if (err instanceof EphapticError) {
            console.log(err.code, err.message, err.data);
        }
    }
    ```

=== "Python"

    ```python
    from ephaptic import EphapticError

    try:
        await client.withdraw(500)
    except EphapticError as err:
        print(err.code, err.message, err.data)
    ```

## Custom errors in your app

Instead of using `ServiceError` for everything, you can extend the `ServiceError` class and set custom defaults. This will give you reusable errors that you can raise anywhere.

```python
from ephaptic import ServiceError

class NotFound(ServiceError):
    code = 'NOT_FOUND'
    message = 'The requested resource was not found.'
    status_code = 404 # used for HTTP callers
```

Now you can just `raise NotFound()`, or override the default `message` wherever you call it with `raise NotFound('No such user.')`.

### Typed `data` for the client types

<div id="#typed-data"></div>

Annotate `data` with a type (typically a Pydantic model) to describe its shape. Register the error with `@ephaptic.error` (or the global `error` decorator) and that shape is carried over into the generated client schema:

```python
from ephaptic import ServiceError

from pydantic import BaseModel

class InsufficientFundsData(pydantic.BaseModel):
    required: int = 50
    available: int

@ephaptic.error
class InsufficientFunds(ServiceError):
    code = 'INSUFFICIENT_FUNDS'
    message = 'You do not have enough funds.'
    status_code = 402
    data: InsufficientFundsData

@ephaptic.expose
async def withdraw(amount: int) -> str:
    if amount > balance:
        raise InsufficientFunds(data=InsufficientFundsData(required=amount, available=balance))
    
    ...
```

After regenerating your schema, the client gets an `EphapticErrors` interface mapping each code to the shape of its `data`:

```typescript title="ephaptic.d.ts"
export interface EphapticErrors {
  INSUFFICIENT_FUNDS: InsufficientFundsData;
}
```

Which you can use to narrow inside a catch block:

```typescript
import { EphapticError } from '@ephaptic/client';
import type { EphapticErrors } from './schema';

try {
    await client.withdraw(500);
} catch (err) {
    if (err instanceof EphapticError && err.code === 'INSUFFICIENT_FUNDS') {
        const data = err.data as EphapticErrors['INSUFFICIENT_FUNDS'];
        console.log(`Need ${data.required}, have ${data.available}`);
    }
}
```

!!! info "What about unhandled exceptions?"
    If your code raises something that *isn't* a `ServiceError` and has no handler, the client receives a generic `INTERNAL` error with **no details**. The full traceback is only logged on the server. This prevents leaking internals to your frontend. You can flip on [debug mode](#debug-mode) during development, if you want to send the details through for faster debugging.


## Errors in the Router (HTTP + RPC)

Since a [Router](router.md) function serves both RPC and HTTP callers, ephaptic makes `ServiceError` work in both worlds:

- Over **RPC**, it's serialized to `{ code, message, data }` and re-raised on the client as an `EphapticError`.
- Over **HTTP**, FastAPI handles it natively: the response uses the error's `status_code` and a matching `{ code, message, data }` JSON body.

```python
@router.get('/items/{id}')
async def get_item(id: int) -> Item:
    item = await db.find(id)
    if not item:
        raise NotFound(data={'id': id}) # RPC -> EphapticError, HTTP -> 404
    return item
```

FastAPI's own `HTTPException` is understood too. When raised in an RPC call it becomes an `EphapticError` with code `HTTP_<status>` (e.g. `HTTP_404`); over HTTP it behaves exactly as it always has.

## Custom exception handlers

Sometimes an exception comes from code you don't control (a library, an ORM). Register a handler with `@ephaptic.exception_handler`, mirroring FastAPI's `@app.exception_handler`:

```python
from ephaptic import ServiceError

@ephaptic.exception_handler(TimeoutError)
def handle_timeout(exc):
    return ServiceError('The upstream service timed out.', code='UPSTREAM_TIMEOUT', status_code=504)
```

A handler can return a `ServiceError`, a `{ code, message, data }` dict, or a normal response object.

### Resolution order

When an exposed function raises during an RPC call, ephaptic resolves the error in this order:

1. Is it a `ServiceError` (or a subclass)? Then it is directly serialized and returned.
2. Is it a FastAPI `HTTPException`? Then it is mapped to a `ServiceError` with code `HTTP_xxx`.
3. Is there an `@ephaptic.exception_handler` registered that matches the error? Then that handler is used, and its response is returned.
4. Is there an `@app.exception_handler` on the FastAPI app that matches the error? Then that handler is used, and its response is returned.
5. If nothing matched, a generic `INTERNAL` error is returned. The stack trace and other details can be exposed when using [debug mode](#debug-mode).

## Debug mode

By default, unhandled exceptions never send their message or traceback to the client. During development you enable debug mode which shows exceptions:

```python
ephaptic = Ephaptic.from_app(app, debug=True)
```

You can set `debug=True` for the ephaptic instance, or you can toggle `app.debug = True` for your FastAPI app.

When you are in debug mode, any unhandled exception's `message` includes the exception's type and its text, and the `data.traceback` contains the full stack traceback. Avoid using this mode in production, as it can leak sensitive data.