# Parameters & Pydantic

## Type Safety

In [first steps](first-steps.md), we built our first RPC app. The app had functionality to add two numbers.

Do you remember the type hinting?

```python
async def add(a: int, b: int) -> int:
```

By using these types, we tell TypeScript:

1. The first argument is named `a` and must be a `number` type, and is required.
2. The second argument is named `b` and must be a `number` type, and is required.
3. The function returns a type `number`.

You can see this by hovering over the `add` function in your IDE:

> `(method) EphapticService.add(a: number, b: number): Promise<number>`

If we had done `await client.add("2", "3");` in TypeScript, we would get an error:

> Argument of type 'string' is not assignable to parameter of type 'number'.

This is called **type-safety**.

## Parameters

Let's try changing the type hints in Python.

```python
from typing import Optional

async def add(a: int, b: Optional[int]) -> int:
    return a + b if b else a
```

Look what happens. Instantly. Hover over the `add` function in your IDE, and you'll see this:

> `(method) EphapticService.add(a: number, b: number | null): Promise<number>`

!!! tip
    If you don't see anything, make sure both the type-generation commands from earlier, with the `--watch` flag.
    If you still don't see anything, make sure your TypeScript-enabled (e.g. Intellisense) extension is enabled. We recommend VSCode for this.

What did we change on the TypeScript side? Nothing! But now, the second `b` parameter has the `number | null` type.

What does this mean? Well, try this:

```typescript
await client.add(num1, null);
```

Since we've now stated that the second number is allowed to be nothing, we can safely pass `null` through. And, if you try running this, you'll see simply the first number (`2`) logged to your browser.

## Pydantic

This is cool, but imagine a real-world app. You have a database, with models for various things - for example, users!

Let's imagine here's your user model.

```python
from pydantic import BaseModel

class User(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
```

Now, let's say we have a function to retrieve a user.

!!! tip
    In [Authentication](security.md), we cover retrieving the currently logged in user in more detail.
    For now, let's assume the current user is the first one in the database.

```python
class User(...):
    ...

database = {} # Obviously, in a real app, you'd have a real database here.
database["users"] = [User(id=1, username="admin", email=None)]

@ephaptic.expose
async def get_user() -> User: # Notice we've type-hinted our return value: `User`.
    return database["users"][0]
```

Now, how would we access the user on the frontend?

```typescript
const user = await client.get_user();
```

If you hover over the `get_user` function, you'll see the method signature:

> `(method) EphapticService.get_user(): Promise<User>`

The function returns a User object. Even the name `User` has synced across languages!

Now, hover over the `user` variable.

> const user: User

Let's try to display information about the user.

=== "React"

    ```tsx
    return (
        <div className="user-view">
            <h2>{user.username}</h2>
            <small>{user.email}</small>
        </div>
    );
    ```

=== "Svelte"

    ```html
    <div class="user-view">
        <h2>{user.username}</h2>
        <small>{user.email}</small>
    </div>
    ```

There's a problem, though. If you hover the `user` here, you'll spot an error:

> Cannot find name 'user'.

Additionally, you won't receive autocomplete when typing out user properties, because TypeScript doesn't know what you mean by `user`.

This is because the `user` is being defined still in our `onMount` function, but it's not a global variable.

Let's define it globally.

=== "React"

    ```typescript
    import { useState, useEffect } from "react";
    // We can import the `User` type from the schema with the same name as our model.
    import type { EphapticService, User } from "./schema";

    function App() {
        const [user, setUser] = useState<User | null>(null);

        useEffect(() => {
            async function load() {
                const data = await client.get_user();
                setUser(data);
            }
            load();
        }, []);
    }
    ```

=== "Svelte"

    ```typescript
    // We can import the `User` type from the schema with the same name as our model.
    import type { EphapticService, User } from "$lib/schema";

    let user: User | null = null;

    onMount(async () => {
        user = await client.get_user();
    });
    ```

But, the HTML snippet remains underlined in red? Let's check the new error.

> 'user' is possibly 'null'.

This is because we defined the type as `User | null`.

To fix this, let's render conditionally, and also add a loading message.

=== "React"

    ```tsx
    if (!user) return (<p>Loading...</p>)
    return (
        <div className="user-view">
            <h2>{user.username}</h2>
            <small>{user.email || "No email set."}</small>
        </div>
    );
    ```

=== "Svelte"

    ```html
    {#if !user}
        <p>Loading...</p>
    {:else}
        <div class="user-view">
            <h2>{user.username}</h2>
            <small>{user.email || "No email set."}</small>
        </div>
    {/if}
    ```

!!! tip
    You may also wish to use a proper state handler, like TanStack Query, to clean up this process.