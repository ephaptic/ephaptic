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

@ephaptic.expose
async def emit_typed_event(value: int):
    await ephaptic.to("user123").emit(MyTypedEvent(value=value))

@ephaptic.expose
def get_user_id() -> str:
    return str(active_user)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv('TEST_PORT', 8000)))