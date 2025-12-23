Now that we know how to call functions from the backend on the frontend, let's try something somewhat reversed: **sending data from the backend to the frontend**.

Okay, so first, let's define the event that we want to send.

!!! tip
    Keep your current imports the same, just add these extras if necessary. Don't replace your code, add to it!

```python title="backend/src/app.py"
from pydantic import BaseModel

...

@ephaptic.event
class SomethingHappened(BaseModel): # (1)
    code: int = 1
    message: str
    isError: bool = False

# Now, broadcast the event.

@ephaptic.expose
async def broadcastEvent(event: SomethingHappened) -> None:
    await ephaptic.emit(event)
```

1. As you would know from [Parameters & Pydantic](parameters.md), there are lots of fields you can put in here.

We can also use the [identity loading](security.md) feature to broadcast to specific user(s):

```python
await ephaptic.to(1).emit(event) # (1)
```

1. Here, `1` is the user ID that we want to emit to. You can provide a list, or a group of args. Ex. `.to(1, 2, 3)`, or `.to([1, 2, 3])`

But what does this do?

Well, since ephaptic allows you to use Pydantic models as function inputs, on the TypeScript end, you can simply call:

```typescript
await client.broadcastEvent({ message: "John created an account!" });
```

And you'll receive full TypeScript safety (autocomplete, missing parameters, etc.) on the event parameter.

Now, how do we *receive* the event?

It's simple!


```typescript
client.on('SomethingHappened', data => {
    console.log(data.message);
})
```

Notice that as soon as you type `client.on('`, the autocomplete knows that the only event registered, and therefore the only thing you can type, is `SomethingHappened`, so if you press `TAB` it fills in the event name for you. Additionally, the `data` is now of type `SomethingHappened` (again, if you wish, you can import it from the schema).

For a proper demonstration with your framework of choice, you should use lifecycle hooks to clean up the listener when the component unmounts.

=== "React"

    ```tsx title="frontend/src/App.tsx"
    import { useEffect } from "react";
    import type { SomethingHappened } from './schema';

    function App() {
        useEffect(() => {
            const handleEvent = (data: SomethingHappened) => {
                console.log(data.message);
            };

            client.on('SomethingHappened', handleEvent);

            return () => {
                client.off('SomethingHappened', handleEvent);
            };
        }, []);

        return <h1>Listening for events...</h1>;
    }
    ```

=== "Svelte"

    ```html title="src/routes/+page.svelte"
    <script lang="ts">
        import { onMount } from 'svelte';
        import type { SomethingHappened } from './schema';

        onMount(() => {
            const handleEvent = (data: SomethingHappened) => {
                console.log(data.message);
            };

            client.on('SomethingHappened', handleEvent);

            return () => {
                client.off('SomethingHappened', handleEvent);
            };
        });
    </script>

    <h1>Listening for events...</h1>
    ```