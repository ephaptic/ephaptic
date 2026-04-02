# Replacing Rest

Why would you even want to use Ephaptic instead of just using standard old REST? Well, it's all about DX (and type-safety!)

Typically, if you wanted to use a FastAPI backend with a TypeScript frontend, your code would look something like this:

```python
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse

class User(BaseModel):
    id: int
    username: str
    email: Optional[str] = None

# now have a function that returns a user by username
userRouter = APIRouter(prefix='/users')

@userRouter.get('/{id}')
async def get_user(id: int) -> User:
    try:
        return await some_internal_helper(id)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
```

```typescript
// ... define your pydantic models again as typescript interfaces and manually map the types ...
interface User {
  id: number;
  username: string;
  email?: string | null;
}
// if they drift out of sync you'll never know until it's too late

// now call the endpoint
async function getUser(id: number): Promise<User> {
    try {
        const res = await fetch(`/users/${id}`); // be careful for sanitization !! in reality we have to use encodeURIComponent or else a single `/` could result in a 404.
        // it could have been even more complex: `await fetch('...', { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });`
        // even worse: having to serialize byte data (e.g. user uploaded profile image) as base64. extra overhead (+33% size) + takes longer to encode/decode
        let data: any;
        try {
            data = await res.json();
        } catch (err: any) {
            // ofc, handle errors!
            throw new Error("failed to decode JSON!"); // boilerplate code that is basically never reached in production but forced to write it anyway just in case
        }
        if (!res.ok || data?.error) throw new Error(data?.error || "something went wrong and we don't know what!!"); // no guarantees
        if (!isUser(data)) { // and you have to write a thing here that checks if the object is of the correct data type. or don't. but then you lose some type safety. so you have to use something like zod
            // probably the interface is not synced with the pydantic model !!! but what do we throw here
            throw new Error("[dev] !!!!! types not synced properly ! or the server is responding with a malformed JSON object")
        }
        // FINALLY we got the user
        return data as User;
    } catch (err: any) {
        throw new Error(err.message || 'unepxcted network error :(');
    }
}
```

Or, if you manage to get through the convoluted stack involving:

- FastAPI's OpenAPI JSON schema generation
- `openapi-typescript` or another tool watches that file and generates types (but you have to use something like `http://localhost:8000/openapi.json` which means spinning up the server each time you want to update types)
- use those types with a tool like `axios` so that typescript knows the return type

(and even then you don't get instant type updating with something like file watchers)

Then... well done to you. But it's way more complex, with more config files, tool friction, points of failure, and the syntax is still going to be verbose than it needs to be.

Ephaptic does all of this at once under one tool.

The best way to use Ephaptic to replace FastAPI with minimal syntax differences is using [The Router](../tutorial/router.md).

Here is the above code but with Ephaptic instead:

```python
from pydantic import BaseModel
from typing import Optional
from ephaptic import Ephaptic
from ephaptic.ext.fastapi import Router
from ephaptic.ctx import is_http
from fastapi import FastAPI
from fastapi.responses import JSONResponse

class User(BaseModel):
    id: int
    username: str
    email: Optional[str] = None

# let's say we have access to the actual app objects too
app = FastAPI()
ephaptic = Ephaptic.from_app(app)

# ephaptic router still supports all APIRouter configuration
userRouter = Router(ephaptic, prefix='/users')

@userRouter.get('/{id}')
async def get_user(id: int) -> User: # same type hinting works for ephaptic and FastAPI
    try:
        return await some_internal_helper(id)
    except Exception as e:
        # you can just raise a normal exception here.
        # raise Exception('somthing happened !!')
        # but if you really want the status code to carry over (for HTTP clients):
        if is_http():
            return JSONResponse(status_code=500, content={'error': str(e)})
        else:
            raise e # raise the error itself so ephaptic can handle it
```

You'd also have this command running (watcher):
```shell
$ ephaptic generate src.app:ephaptic -o ../frontend/src/lib/schema.d.ts
```

```typescript
import type { User, EphapticService } from '$lib/schema'; // this is Svelte import ('$lib'), but obviously you can replace with whatever you'd use
import { connect } from '@ephaptic/client'; // or you may have a singleton pattern (recommended)

const client = connect() as unknown as EphapticService;

// now call the endpoint
async function getUser(id: number): Promise<User> {
    return await client.get_user(id);
}

// since we type hinted the input parameter type + the return type in the router, typescript will be fine with this. in fact this `client.get_user` has the exact same parameters and return type. so we don't even need this wrapper function:

// in code that uses the function:

// replace
const user = await getUser(0);
// with
const user = await client.get_user(0);

// if you write:
const user = await getUser('hi');
// typescript will yell at you. but the second you change the input on the backend from `int` to `str` the error will disappear. instantly

// or:
(await getUser(0)).nonexistentproperty;
// again will yell at you
// even the second you write that closing bracket and then the dot, your IDE autocomplete will show all the possible properties (and their types (and docstrings?))
```