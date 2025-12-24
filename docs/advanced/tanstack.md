For those of you who have used tRPC before, you might be familiar with [**TanStack Query**](https://tanstack.com/query).

Luckily for you, ephaptic has *first-class* support for it.

Let's use it.

=== "React"

    ```tsx
    import { connect } from '@ephaptic/client';
    import type { EphapticService } from './schema';
    import { useQuery } from '@tanstack/react-query';

    const client = connect() as unknown as EphapticService;

    function Todos() {
        const { data, isPending, error } = useQuery(client.queries.getTodos()); // You can also pass arguments, the same way you normally would.

        if (isPending) return <span>Loading...</span>;
        if (error) return <span>Oops!</span>;

        return <ul>{data.map(t => <li key={t.id}>{t.title}</li>)}</ul>;
    }

    export default Todos;
    ```

=== "Svelte"

    ```html
    <script lang="ts">
        import { connect } from '@ephaptic/client';
        import type { EphapticService } from '$lib/schema';
        import { createQuery } from '@tanstack/svelte-query';

        const client = connect() as unknown as EphapticService;

        // NOTE: Svelte Query v5 requires you to pass a function first, for reactivity.
        const todos = createQuery(() => { client.queries.getTodos() }); // You can also pass arguments, the same way you normally would.
    </script>

    {#if $todos.isPending}
        Loading...
    {:else if $todos.error}
        Oops!
    {:else}
        <ul>
            {#each $todos.data as t}
                <li>{t.title}</li>
            {/each}
        </ul>
    {/if}
    ```

!!! note
    Both of these examples were taken from the official TanStack Query page for the respective frameworks, and then modified.