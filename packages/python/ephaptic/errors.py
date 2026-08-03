from typing import Any, Optional, Dict

import pydantic

# Reserved error codes which Ephaptic uses under the hood.
RESERVED_CODES = {'INTERNAL', 'VALIDATION_ERROR', 'RETURN_VALIDATION_ERROR', 'RATELIMIT', 'NOT_FOUND'}


class ServiceError(Exception):
    '''
    Base class for typed, structured errors.

    You may subclass it to define your own application errors, overriding the static defaults:

        class NotFound(ServiceError):
            code = 'NOT_FOUND'
            message = 'The requested resource was not found.'
            status_code = 404

    You can also annotate `data` with a type (typically a Pydantic model) so its
    shape is carried over into the generated client schema when the error is
    registered via `@ephaptic.error`:

        class InsufficientFundsData(pydantic.BaseModel):
            required: int
            available: int

        class InsufficientFunds(ServiceError):
            code = 'INSUFFICIENT_FUNDS'
            status_code = 402
            data: InsufficientFundsData

    You can then throw the error either bare (`new InsufficientFunds()`) or with overrides (e.g. `new InsufficientFunds(message="No such object.", data=InsufficientFundsData(required=5, available=0))`)
    '''

    # the default settings for a ServiceError, you can override these in your subclasses
    code: str = 'ERROR'
    message: str = 'An error occurred.'
    status_code: int = 400

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        data: Any = None,
        status_code: Optional[int] = None,
    ):
        cls = type(self)
        self.code = code or cls.code
        self.message = message if message is not None else cls.message
        self.status_code = status_code if status_code is not None else cls.status_code
        self.data = data
        super().__init__(self.message)

    def to_wire(self) -> Dict[str, Any]:
        data = self.data
        if isinstance(data, pydantic.BaseModel):
            data = data.model_dump(mode='python')
        return {'code': self.code, 'message': self.message, 'data': data}


class RatelimitExceededException(ServiceError):
    code = 'RATELIMIT'
    message = 'Rate limit exceeded.'
    status_code = 429

    def __init__(self, message: str, retry_after: int):
        super().__init__(message, data={'retry_after': retry_after})
        self.retry_after = retry_after


class EphapticError(Exception):
    def __init__(self, code: str, message: str = '', data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f'[{code}] {message}' if message else code)

    @classmethod
    def from_wire(cls, error: Any) -> 'EphapticError':
        if isinstance(error, dict):
            return cls(error.get('code', 'ERROR'), error.get('message', ''), error.get('data'))

        return cls('ERROR', str(error), None)