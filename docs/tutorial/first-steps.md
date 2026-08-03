# First Steps

Let's build a simple app.

We'll create a function on the backend that adds two numbers together, and then call it on the frontend.

## The Backend

Let's create the FastAPI backend. We'll use [uv](https://docs.astral.sh/uv/) to manage the project and its dependencies.

Run this in your project directory:

```console
$ uv init backend
$ cd backend
$ uv add ephaptic "fastapi[standard]"
$ mkdir -p src
```

Now, create `backend/src/app.py` in your favourite editor.

!!! tip
    Don't have `uv`? Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`. Prefer `pip`? Create a virtual environment inside `backend/` and `pip install ephaptic "fastapi[standard]"`, then run the commands below without `uv run`.

```python title="backend/src/app.py"
from fastapi import FastAPI
from ephaptic import Ephaptic

app = FastAPI()
ephaptic = Ephaptic.from_app(app)

# Use the decorator to expose the function to the frontend!
@ephaptic.expose
async def add(a: int, b: int) -> int: # Type hint our function!
    return a + b
```

That's *literally* it. No routes, no REST methods, no JSON parsing, no boilerplate that you're used to.

## Generate the Schema

The backend defines the API, so the schema must generate with that as its reference. From the `backend` directory, run this:

```console
$ uv run ephaptic generate src.app:ephaptic -o schema.json --watch
Watching for changes (/home/user/ephaptic-demo/backend)...
Attempting to import `ephaptic` from `src.app`...
Functions
- async def add(a: int, b: int) -> int
Schema generated to `schema.json`.
```

Or see it rendered in colour:

<code>
<span>$ uv run ephaptic generate src.app:ephaptic -o schema.json --watch</span><br/>
<span>Watching for changes (/home/user/ephaptic-demo/backend)...</span><br/>
<span>Attempting to import `ephaptic` from `src.app`...</span><br/>
<span><b style="color: #3b8eea">Functions</b></span><br/>
<span>&nbsp;&nbsp;<span style="color: #8a8a8a">-</span> <span style="color: #c586c0">async </span><span style="color: #3b8eea">def </span><span style="color: #dcdcaa">add</span>(<span style="color: #4fc1ff">a</span>: <span style="color: #4ec9b0">int</span>, <span style="color: #4fc1ff">b</span>: <span style="color: #4ec9b0">int</span>) -> <span style="color: #4ec9b0">int</span></span><br/>
<span><span style="color: #23d18b">Schema generated to `schema.json`.</span></span>
</code>

You can also point `-o` at a `.d.ts` file to skip the JSON layer entirely.

For more info about the CLI, head to [The CLI](../advanced/cli.md).

## The Frontend

Now, let's build the frontend app that will run this. Open a **new terminal** at your project root (leave the schema watcher running in the backend one).

!!! tip
    ephaptic is framework independent. You don't have to use React or Svelte - any framework/library will work!
    You can even do it in native JS with the CDN:
    ```html
    <script type="module">
        import { connect } from 'https://cdn.jsdelivr.net/npm/@ephaptic/client/+esm';

        const client = connect();
    </script>
    ```
    Just be aware that if you opt for native browser JS, you won't receive type support (JavaScript does not support types.)

=== "React"

    ```console
    $ npm create vite@latest frontend
    Need to install the following packages:
    create-vite@8.2.0
    Ok to proceed? (y) 


    > npx
    > "create-vite" frontend

    │
    ◇  Select a framework:
    │  React
    │
    ◇  Select a variant:
    │  TypeScript
    │
    ◇  Use rolldown-vite (Experimental)?:
    │  No
    │
    ◇  Install with npm and start now?
    │  Yes
    │
    ◇  Scaffolding project in /home/user/ephaptic-demo/frontend...
    │
    ◇  Installing dependencies with npm...

    added 175 packages, and audited 176 packages in 26s

    45 packages are looking for funding
    run `npm fund` for details

    found 0 vulnerabilities
    │
    ◇  Starting dev server...

    > frontend@0.0.0 dev
    > vite

    VITE v7.3.0  ready in 172 ms

    ➜  Local:   http://localhost:5173/
    ➜  Network: use --host to expose
    ➜  press h + enter to show help
    ^C

    $ cd frontend
    $ npm i
    $ npm i @ephaptic/client
    ```

=== "Svelte"

    ```console
    $ mkdir frontend
    $ cd frontend
    $ npx sv create
    Need to install the following packages:
    sv@0.11.0
    Ok to proceed? (y) 


    ┌  Welcome to the Svelte CLI! (v0.11.0)
    │
    ◇  Where would you like your project to be created?
    │  ./
    │
    ◇  Which template would you like?
    │  SvelteKit minimal
    │
    ◇  Add type checking with TypeScript?
    │  Yes, using TypeScript syntax
    │
    ◇  What would you like to add to your project? (use arrow keys / space bar)
    │  none
    │
    ◆  Project created
    │
    ◇  Which package manager do you want to install dependencies with?
    │  npm
    │
    │  npx sv create --template minimal --types ts --install npm ./
    │
    │
    ◆  Successfully installed dependencies with npm
    │
    ◇  What's next? ───────────────────────────────╮
    │                                              │
    │  📁 Project steps                            │
    │                                              │
    │    1: npm run dev -- --open                  │
    │                                              │
    │  To close the dev server, hit Ctrl-C         │
    │                                              │
    │  Stuck? Visit us at https://svelte.dev/chat  │
    │                                              │
    ├──────────────────────────────────────────────╯
    │
    └  You're all set!
    $ npm i @ephaptic/client
    ```


We'll generate the TypeScript definitions from that schema. Since ephaptic lives in the backend, run this from your `backend` directory (add `--watch` to keep them in sync as you code):

=== "React"

    ```console
    $ uv run ephaptic generate schema.json -o ../frontend/src/schema.d.ts
    Schema generated to `../frontend/src/schema.d.ts`.
    ```

=== "Svelte"

    ```console
    $ uv run ephaptic generate schema.json -o ../frontend/src/lib/schema.d.ts
    Schema generated to `../frontend/src/lib/schema.d.ts`.
    ```

Now, we can finally use the client.

=== "React"

    ```typescript title="frontend/src/App.tsx"
    import { connect } from "@ephaptic/client";
    import { type EphapticService } from "./schema";
    import { useEffect } from "react";

    const client = connect({
        url: "ws://localhost:8000/_ephaptic"
    }) as EphapticService;

    function App() {
        useEffect(() => {
            async function calculate() {
                const num1 = 2;
                const num2 = 3;
                const result = await client.add(num1, num2);
                console.log(result);
            }
            
            calculate();
        }, []);

        return <h1>Check the console!</h1>;
    }

    export default App;
    ```

=== "Svelte"

    ```html title="frontend/src/routes/+page.svelte"
    <script lang="ts">
        import { connect } from "@ephaptic/client";
        import { type EphapticService } from "$lib/schema";
        import { onMount } from 'svelte';

        const client = connect({
            url: "ws://localhost:8000/_ephaptic"
        }) as EphapticService;

        onMount(async () => {
            const num1 = 2;
            const num2 = 3;

            const result = await client.add(num1, num2);

            console.log(result);
        });
    </script>

    <h1>Check the console!</h1>
    ```


!!! info "Going to Production?"
    In development, we hardcoded `ws://localhost:8000` because the frontend (port 5173) and backend (port 8000) are separate.

    In production (e.g., Docker), you should use a **Reverse Proxy** (like Nginx or Traefik) to route traffic.
    
    *   Route `/` -> Frontend Container
    *   Route `/_ephaptic` -> Backend Container

    This allows you to revert to `const client = connect();` (without arguments), as the browser will correctly infer the host and port relative to the current page.

    Alternatively, if your backend and your frontend are on different hosts, you can specify it: `connect({ url: 'wss://my-backend.app/_ephaptic' })`.

    Remember you should also tell Ephaptic which headers that the proxy sends the connecting IP address behind, to ensure that ratelimiting works properly.

    Learn more in the [Deployment](../advanced/deployment.md) section.

!!! tip
    Notice that if you try to pass a string like `client.add("2", 3)`, your editor will scream at you. That's the power of **ephaptic**.
    We'll learn more about this in [the next chapter](parameters.md).

## Run the app

Now that we've added all the code, let's fire everything up! You'll need two terminals - one for the backend, one for the frontend.

From your `backend` directory:

```console
$ uv run uvicorn src.app:app --reload --port 8000
INFO:     Will watch for changes in these directories: ['/home/user/ephaptic-demo/backend']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [1] using WatchFiles
INFO:     Started server process [2]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

And from your project root:

```console
$ cd frontend
$ npm run dev

> frontend@0.0.1 dev
> vite dev

12:00:00 [vite] (client) Forced re-optimization of dependencies

  VITE v7.3.0  ready in 1000 ms

  ➜  Local:   http://localhost:5173/
  ➜  Network: use --host to expose
  ➜  press h + enter to show help
```

Now, open [http://localhost:5173](http://localhost:5173) in your browser. Check the console (`F12`). You should see the result of the addition logged!

!!! success "Congratulations!"
    Well done! We've just built a full-stack, low-latency, type-safe app without writing a single API route or serializers!