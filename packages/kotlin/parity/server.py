import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "python"))

from ephaptic import Ephaptic, ServiceError, active_user


class NotEnoughFunds(ServiceError):
    code = "INSUFFICIENT_FUNDS"
    message = "Not enough funds."
    status_code = 402


ephaptic = Ephaptic()


@ephaptic.identity_loader
def load_user(auth):
    if isinstance(auth, dict):
        return auth.get("token")
    return auth


@ephaptic.expose
async def add(a: int, b: int) -> int:
    return a + b


@ephaptic.expose
async def echo(message):
    return message


@ephaptic.expose
async def whoami():
    return active_user()


@ephaptic.expose
async def returns_null():
    return None


@ephaptic.expose
async def boom():
    raise NotEnoughFunds("You are broke.", data={"available": 3})


@ephaptic.expose
async def kaboom():
    raise ValueError("secret sk-proj-abcd")


@ephaptic.expose(requires_login=True)
async def secret():
    return "sk-proj-1234"


@ephaptic.expose
async def countdown(n: int):
    for i in range(n, 0, -1):
        yield i


@ephaptic.expose
async def falsy_stream():
    for value in [0, None, 1]:
        yield value


@ephaptic.expose
async def badstream():
    yield 1
    raise NotEnoughFunds("stream broke")


@ephaptic.expose
async def ping():
    # The dynamic emitter, addressed at the caller's own identity.
    await ephaptic.to(active_user()).pong(ok=True)
    return "sent"


PORT = int(os.environ.get("KT_PORT", "7900"))


async def main():
    print(f"READY {PORT}", flush=True)
    await ephaptic.listen(port=PORT)


asyncio.run(main())