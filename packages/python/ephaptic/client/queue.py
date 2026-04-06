import asyncio

class AsyncQueue:
    def __init__(self):
        self._queue = asyncio.Queue()

    def __aiter__(self):
        return self
    
    async def __anext__(self):
        chunk = await self._queue.get()
        
        if chunk is StopAsyncIteration:
            raise StopAsyncIteration
        
        if isinstance(chunk, Exception):
            raise chunk
        
        return chunk
    
    def push(self, data):
        self._queue.put_nowait(data)

    def close(self):
        self._queue.put_nowait(StopAsyncIteration)

    def throw(self, error: Exception):
        self._queue.put_nowait(error)