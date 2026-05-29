from typing import Any

class Encoding:
    def encode(self, data: Any) -> bytes: raise NotImplementedError()
    def decode(self, data: bytes) -> Any: raise NotImplementedError()