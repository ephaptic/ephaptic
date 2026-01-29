from typing import *
import inspect
import pydantic
from .utils import parse_limit

F = TypeVar('F', bound=Callable[..., Any])
M = TypeVar('M', bound=Type[pydantic.BaseModel])

META_KEY = '_ephaptic_metadata'

class Expose:
    def __init__(self, registry: Dict[str, Callable]):
        self.registry = registry

    @overload
    def __call__(self, func: F) -> F:
        ...

    @overload
    def __call__(
        self,
        *,
        name: Optional[str] = None,
        response_model: Optional[type] = None,
        rate_limit: Optional[str] = None,
    ):
        ...

    def __call__(self, func=None, **kwargs):
        def inject(f: F) -> F:
            self.registry[kwargs.get('name') or f.__name__] = f

            if kwargs.get('rate_limit'): kwargs['rate_limit'] = parse_limit(kwargs['rate_limit'])

            meta = getattr(f, META_KEY, {})
            meta.update(kwargs)
            setattr(f, META_KEY, meta)

            return f
        
        if func is not None and callable(func):
            return inject(func)
        
        return inject
    
class Event:
    def __init__(self, registry: Dict[str, Type[pydantic.BaseModel]]):
        self.registry = registry


    @overload
    def __call__(self, model: M) -> M:
        ...

    @overload
    def __call__(
        self,
        *,
        name: Optional[str] = None,
    ) -> Callable[[M], M]:
        ...

    def __call__(self, model=None, **kwargs):
        def inject(m: M) -> M:
            self.registry[kwargs.get('name') or m.__name__] = m

            meta = getattr(m, META_KEY, {})
            meta.update(kwargs)
            setattr(m, META_KEY, meta)

            return m
        
        if model is not None and isinstance(model, type) and issubclass(model, pydantic.BaseModel):
            return inject(model)
        
        return inject


class IdentityLoader:
    def __init__(self, setter: Callable[[Callable], None]):
        self.setter = setter

    def __call__(self, func: F) -> F:
        self.setter(func)
        return func