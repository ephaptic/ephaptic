from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from ephaptic import Ephaptic, active_user, ServiceError
from ephaptic.ctx import is_http, is_rpc
from ephaptic.ext.fastapi import Router
import pydantic, typing, asyncio, time
import os

app = FastAPI()
ephaptic = Ephaptic.from_app(app)

@ephaptic.event
class MyEvent(pydantic.BaseModel):
    message: str

@ephaptic.event
class MyTypedEvent(pydantic.BaseModel):
    value: int

@ephaptic.identity_loader
def load_user(auth: str):
    return auth

@ephaptic.http_identity_loader
def load_http_user(request: Request):
    token = request.headers.get('Authorization')
    if not token: return None
    return token.removeprefix('Bearer ')

@ephaptic.expose
async def echo(message: str) -> str:
    return message

@ephaptic.expose
async def add(a: int, b: int) -> int:
    return a + b

@ephaptic.expose
async def divide(a: int, b: int) -> float:
    """Divide one number by another.

    Args:
        a: The numerator.
        b: The denominator (must be non-zero).

    Returns:
        The quotient of a and b.
    """ # test docstrings
    return a / b

@ephaptic.expose
async def emit_event(message: str):
    await ephaptic.to("user123").emit(MyEvent(message=message))

@ephaptic.expose() # test as function
async def emit_typed_event(value: int):
    await ephaptic.to("user123").emit(MyTypedEvent(value=value))

class MyTestObject(pydantic.BaseModel):
    text: str
    num: typing.Optional[int] = None
    default: str = "DEFAULT"

@ephaptic.expose
async def test_pydantic(test_object: MyTestObject) -> MyTestObject:
    return MyTestObject(text=test_object.text, num=test_object.num) # There are sooo many ways to do this better,
                                                                    # I'm just doing this to verify that the object is a Pydantic model.

@ephaptic.expose(name='get_user_id') # test with name kwarg
def get_uid() -> str:
    return active_user()

@ephaptic.expose(rate_limit='1/m') # 1 per minute
async def spam_me() -> str: return 'ok'

@ephaptic.expose
async def async_generator() -> typing.AsyncGenerator[str, None]:
    for message in ['Message A', 'Message B']:
        await asyncio.sleep(1)
        yield message

@ephaptic.expose
def sync_generator() -> typing.Generator[MyTestObject, None, None]:
    for i, message in enumerate(['Message C', 'Message D']):
        time.sleep(1)
        yield MyTestObject(text=message, num=i)

@ephaptic.expose
async def failing_stream() -> typing.AsyncGenerator[str, None]:
    yield 'first'
    raise ServiceError('The stream broke. o noez :(', code='STREAM_FAILED', data={'after': 'first'})

@ephaptic.expose
async def falsy_stream() -> typing.AsyncGenerator[typing.Optional[int], None]:
    for value in (0, None, 1):
        yield value

@ephaptic.expose
async def returns_none() -> typing.Optional[str]:
    return None

@ephaptic.expose(requires_login=True)
async def rpc_secret() -> str:
    return 'rpc-secret'


class InsufficientFundsData(pydantic.BaseModel):
    required: int
    available: int

@ephaptic.error
class InsufficientFunds(ServiceError):
    code = 'INSUFFICIENT_FUNDS'
    message = 'You do not have enough funds.'
    status_code = 402
    data: InsufficientFundsData

@ephaptic.expose
async def withdraw(amount: int) -> str:
    if amount > 100:
        raise InsufficientFunds(data=InsufficientFundsData(required=amount, available=100))
    return 'ok'

@ephaptic.expose
async def raise_unhandled() -> str:
    raise ValueError('secret internal detail')

@ephaptic.expose
async def raise_http() -> str:
    raise HTTPException(status_code=404, detail='Nope')

class CustomError(Exception): ...

@ephaptic.exception_handler(CustomError)
def handle_custom(exc):
    return ServiceError('Handled by ephaptic.', code='CUSTOM_HANDLED', status_code=400, data={'handled': True})

@ephaptic.expose
async def raise_custom() -> str:
    raise CustomError('boom')

class AppLevelError(Exception): ...

@app.exception_handler(AppLevelError)
async def app_level_handler(request, exc):
    return JSONResponse(status_code=418, content={'code': 'APP_LEVEL', 'message': 'from app handler', 'data': {'teapot': True}})

@ephaptic.expose
async def raise_app_level() -> str:
    raise AppLevelError()


router = Router(ephaptic)

@router.get('/r_echo', requires_login=True)
def r_echo(message: str) -> dict:
    return {
        "is_rpc": is_rpc(),
        "is_http": is_http(),
        "active_user": active_user(),
        "message": message,
    }

@router.get('/r_asyncgen')
async def r_async_generator() -> typing.AsyncGenerator[str, None]:
    for message in ['Message A', 'Message B']:
        await asyncio.sleep(1)
        yield message

@router.get('/r_syncgen')
def r_sync_generator() -> typing.Generator[MyTestObject, None, None]:
    for i, message in enumerate(['Message C', 'Message D']):
        time.sleep(1)
        yield MyTestObject(text=message, num=i)

class MyFakeTestObject: # Not a BaseModel
    text: str
    num = 0

@router.get('/r_test_custom')
async def r_test_custom() -> MyTestObject:
    object = MyFakeTestObject()

    object.text = 'Custom'

    return object # Should return a mapped Pydantic version of the object, where object.default == "DEFAULT"

ephaptic.include(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv('TEST_PORT', 8000)))