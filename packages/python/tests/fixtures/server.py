from fastapi import FastAPI
from ephaptic import Ephaptic, active_user
import pydantic
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

@ephaptic.expose(name='get_user_id') # test with name kwarg
def get_uid() -> str:
    return str(active_user)

@ephaptic.expose(rate_limit='1/m') # 1 per minute
async def spam_me() -> str: return 'ok'

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv('TEST_PORT', 8000)))