from fastapi import FastAPI, Request
from ephaptic import Ephaptic, active_user
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
async def emit_event(message: str):
    await ephaptic.to("user123").emit(MyEvent(message=message))

@ephaptic.expose() # test as function
async def emit_typed_event(value: int):
    await ephaptic.to("user123").emit(MyTypedEvent(value=value))

class MyTestObject(pydantic.BaseModel):
    text: str
    num: typing.Optional[int] = None

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
def sync_generator() -> typing.Generator[MyTestObject, None]:
    for i, message in enumerate(['Message C', 'Message D']):
        time.sleep(1)
        yield MyTestObject(text=message, num=i)


router = Router(ephaptic)

@router.get('/r_echo', requires_login=True)
def r_echo(message: str) -> dict:
    return {
        "is_rpc": is_rpc(),
        "is_http": is_http(),
        "active_user": active_user(),
        "message": message,
    }

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv('TEST_PORT', 8000)))