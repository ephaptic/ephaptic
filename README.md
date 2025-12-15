<div align="center">
    <a href="https://github.com/ephaptic/ephaptic">
        <picture>
            <img src="https://raw.githubusercontent.com/ephaptic/ephaptic/refs/heads/main/.github/assets/logo.png" alt="ephaptic logo" height="200">
            <!-- <img src="https://avatars.githubusercontent.com/u/248199226?s=256" alt="ephaptic logo" height="200> -->
        </picture>
    </a>
<br>
<h1>ephaptic</h1>
<br>
<a href="https://github.com/ephaptic/ephaptic/blob/main/LICENSE"><img alt="GitHub License" src="https://img.shields.io/github/license/ephaptic/ephaptic?style=for-the-badge&labelColor=%23222222"></a> <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/ephaptic/ephaptic/publish-js.yml?style=for-the-badge&label=NPM%20Build%20Status&labelColor=%23222222"> <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/ephaptic/ephaptic/publish-python.yml?style=for-the-badge&label=PyPI%20Build%20Status&labelColor=%23222222"> <a href="https://pypi.org/project/ephaptic/">
  <img alt="PyPI - Version"
       src="https://img.shields.io/pypi/v/ephaptic?style=for-the-badge&labelColor=%23222222">
</a>

<a href="https://www.npmjs.com/package/@ephaptic/client">
  <img alt="NPM - Version"
       src="https://img.shields.io/npm/v/%40ephaptic%2Fclient?style=for-the-badge&labelColor=%23222222">
</a>


</div>

## What is `ephaptic`?

<br>

<blockquote>
    <b>ephaptic (adj.)</b><br>
    electrical conduction of a nerve impulse across an ephapse without the mediation of a neurotransmitter.
</blockquote>

Nah, just kidding. It's an RPC framework.

> **ephaptic** — Call your backend straight from your frontend. No JSON. Low latency. Invisible middleware.

## Getting Started

- Ephaptic is designed to be invisible. Write a function on the server, call it on the client. No extra boilerplate.

- Plus, it's horizontally scalable with Redis (optional), and features extremely low latency thanks to [msgpack](https://github.com/msgpack).

- Oh, and the client can also listen to events broadcasted by the server. No, like literally. You just need to add an `eventListener`. Did I mention? Events can be sent to specific targets, specific users - not just anyone online.

- Saved the best for last: it's type-safe. Don't believe me? Try it out for yourself. Simply type hint return values and parameters on the backend, and watch those very Python types transform into interfaces and types on the TypeScript frontend. Plus, you can use Pydantic - which means, for those of you who are FastAPI users, this is going to be great.

What are you waiting for? **Let's go.**

<details>
    <summary>Python</summary>
    
#### Client:

```
pip install ephaptic
```

#### Server:

```
pip install ephaptic[server]
```

```python
from fastapi import FastAPI # or `from quart import Quart`
from ephaptic import Ephaptic

app = FastAPI() # or `app = Quart(__name__)`

ephaptic = Ephaptic.from_app(app) # Finds which framework you're using, and creates an ephaptic server.
```

You can also specify a custom path:

```python
ephaptic = Ephaptic.from_app(app, path="/websocket")
```

And you can even use Redis for horizontal scaling!

```python
ephaptic = Ephaptic.from_app(app, redis_url="redis://my-redis-container:6379/0")
```

Now, how do you expose your function to the frontend?

```python
@ephaptic.expose
async def add(num1: int, num2: int) -> int:
    return num1 + num2
```

###### If you're trying to expose functions statelessly, e.g. in a different file, feel free to instead import and use the `expose` function from the library instead of the instance. Please note that if you do this, you must define all exposed functions *before* creating the ephaptic instance - easily done by simply placing your import line above the ephaptic constructor. The same thing can be done with the global `identity_loader` decorator.

Yep, it's really that simple.

But what if your code throws an error? No sweat, it just throws up on the frontend, with the error name.

And, want to say something to the frontend?

```python
await ephaptic.to(user1, user2).notification("Hello, world!", priority="high")
```

To create a schema of your RPC endpoints:

```
$ ephaptic src.app:app -o schema.json
```

Pydantic is entirely supported. It's validated for arguments, it's auto-serialized when you return a pydantic model, and your models receive type definitions in the schema.

</details>

<details>
    <summary>JavaScript/TypeScript — Browser (Svelt, React, Angular, Vite, etc.)</summary>

#### To use with a framework / Vite:

```
$ npm install @ephaptic/client
```

Then:

```typescript
import { connect } from "@ephaptic/client";

const client = connect(); // Defaults to `/_ephaptic`.
```

Or, you can use it with a custom URL:

```typescript
const client = connect({ url: '/ws' });
```

```typescript
const client = connect({ url: 'wss://my-backend.deployment/ephaptic' });
```

You can even send auth objects to the server for identity loading.

```typescript
const client = connect({ url: '...', auth: { token: window.localStorage.getItem('jwtToken') } })
```

And you can load types, too.

```
$ npm i --save-dev @ephaptic/type-gen
$ npx @ephaptic/type-gen ./schema.json -o schema.d.ts
```

```typescript
import { connect } from "@ephaptic/client";
import { type EphapticService } from './schema';

const client = connect(...) as unknown as EphapticService;
```


#### Or, to use in your browser:

```html
<script type="module">
import { connect } from 'https://cdn.jsdelivr.net/npm/@ephaptic/client@latest/+esm';

const client = connect(...);
</script>
```

<!-- TODO: Add extended documentation -->

</details>

## [License](https://github.com/ephaptic/ephaptic/blob/main/LICENSE)

---

<p align="center">
    &copy; ephaptic 2025
</p>
