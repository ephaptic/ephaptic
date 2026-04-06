We've learnt how to send one-sided events from the server to the client.

But what if you wanted to progressively, with low latency, stream data to the client from within an RPC call?

This is where streaming comes from.

Let's imagine we are building an AI chat app.

Obviously, we don't wait for the full LLM response before sending it to the user. We need to stream the response token-by-token as it comes.

This is what we have learnt how to do so far in Ephaptic:

```python
@expose
async def ai_response(message: str) -> str:
    completion = await client.chat.completions.create(
        model = 'llm-1-pro',
        messages = [
            {
                "role": "user",
                "content": message
            }
        ],
    )

    return completion.choices[0].message.content
```

And here is the accompanying client code:

```typescript
const prompt = "What is the capital of France?";

const result = await client.ai_response(prompt);

console.log(result);
```

This will work, but it will not stream the response token-by-token. So, let's learn how to stream.

To stream, we first need to tell Ephaptic that the function is a Generator.

!!! tip
    In this tutorial, we are using `AsyncGenerator`s as our logic is asynchronous, but synchronous generators are entirely supported.


Let's update the decorator:

```python
import typing

async def ai_response(message: str) -> typing.AsyncGenerator[str, None]:
```

In `typing.AsyncGenerator`, the `str` refers to the type of data we will return. The `None` is more complex, but we don't need to learn about this to use generators.

Now, we need to yield each token.

Let's update the function:

```python
@expose
async def ai_response(message: str) -> typing.AsyncGenerator[str, None]:
    stream = await client.chat.completions.create(
        model = 'llm-1-pro',
        messages = [
            {
                "role": "user",
                "content": message
            }
        ],
        stream=True
    )

    async for chunk in stream:
        yield chunk.choices[0].delta.content
```

Notice how now, we `yield` the content of each chunk.

And here is the client code with the new changes:

```typescript
const prompt = "What is the capital of France?";

const stream = await client.ai_response(prompt);

for await (const chunk of stream) {
    console.log(chunk);
}
```

===

It's not just strings that we can stream, however.

We can also stream complex objects, such as your Pydantic models, or really anything JSON-serializable. And, the type generator also supports this.

In your `for await (const x of stream) { ... }` block, your editor will know that the variable `x` is of whatever type you defined it as in Pydantic.