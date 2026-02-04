from contextvars import ContextVar

_scope_ctx = ContextVar('ephaptic_scope', default='rpc')
_active_transport_ctx = ContextVar('active_transport', default=None)
_active_user_ctx = ContextVar('active_user', default=None)

def is_http() -> bool: return _scope_ctx.get() == 'http'
def is_rpc()  -> bool: return _scope_ctx.get() == 'rpc'

def active_user(): return _active_user_ctx.get()