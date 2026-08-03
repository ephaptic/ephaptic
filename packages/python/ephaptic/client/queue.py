import asyncio

class _Closed: ...

class AsyncQueue:
    """
    A very complex thing that I only partially understand. But it does work.
    """

    def __init__(self):
        self._queue = asyncio.Queue()
        self._closed = False
        self._iterator = None
        self.on_abandon = None

    async def _drain(self):
        try:
            while True:
                chunk = await self._queue.get()

                if chunk is _Closed:
                    return

                if isinstance(chunk, BaseException):
                    raise chunk

                yield chunk
        finally:
            self._release()

    def __aiter__(self):
        if self._iterator is None:
            self._iterator = self._drain()
        return self._iterator

    async def __anext__(self):
        return await self.__aiter__().__anext__()

    def push(self, data):
        if self._closed: return
        self._queue.put_nowait(data)

    def close(self):
        if self._closed: return
        self._closed = True
        self._queue.put_nowait(_Closed)

    def throw(self, error: BaseException):
        if self._closed: return
        self._closed = True
        self._queue.put_nowait(error)

    def _release(self):
        self._closed = True
        callback, self.on_abandon = self.on_abandon, None
        if callback is not None:
            try: callback()
            except Exception: ...

    async def aclose(self):
        self._release()
        if self._iterator is not None:
            await self._iterator.aclose()