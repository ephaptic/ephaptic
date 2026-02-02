from contextvars import ContextVar

_scope_ctx = ContextVar('ephaptic_scope', default='rpc')

def is_http() -> bool: return _scope_ctx.get() == 'http'
def is_rpc()  -> bool: return _scope_ctx.get() == 'rpc'