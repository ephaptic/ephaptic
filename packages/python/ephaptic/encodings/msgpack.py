from . import Encoding
from typing import Any
import msgpack

from datetime import datetime, date
from uuid import UUID

def default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    
    if isinstance(obj, UUID):
        return str(obj)
    
    raise TypeError(f"Cannot serialize type '{type(obj)}'.")

class MsgpackEncoding(Encoding):
    def encode(self, data: Any) -> bytes:
        return msgpack.dumps(data, default=default)
    
    def decode(self, data: bytes) -> Any:
        return msgpack.loads(data)