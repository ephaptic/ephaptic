from typing import *
import inspect
import pydantic
from .utils import parse_limit

F = TypeVar('F', bound=Callable[..., Any])
M = TypeVar('M', bound=Type[pydantic.BaseModel])

META_KEY = '_ephaptic_metadata'

EXPOSE_OPTIONS = frozenset({'name', 'response_model', 'rate_limit', 'requires_login', 'hints', 'sig'})

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
        requires_login: bool = False,
        hints: Optional[dict[str, Any]] = None,
        sig: Optional[inspect.Signature] = None,
    ):
        ...

    def __call__(self, func=None, **kwargs):
        unknown = set(kwargs) - EXPOSE_OPTIONS
        if unknown:
            raise TypeError(
                f"Unknown option(s) for @expose: {', '.join(sorted(unknown))}. "
                f"Valid options are: {', '.join(sorted(EXPOSE_OPTIONS))}."
            )

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

class ExceptionHandler:
    def __init__(self, registry: Dict[Type[BaseException], Callable]):
        self.registry = registry

    def __call__(self, exc_type: Type[BaseException]):
        def decorator(func: F) -> F:
            self.registry[exc_type] = func
            return func

        return decorator

class Error:
    def __init__(self, registry: Dict[str, type]):
        self.registry = registry

    def __call__(self, cls=None, *, code: Optional[str] = None):
        def inject(c: type) -> type:
            self.registry[code or getattr(c, 'code', c.__name__)] = c
            return c

        if cls is not None and isinstance(cls, type):
            return inject(cls)

        return inject