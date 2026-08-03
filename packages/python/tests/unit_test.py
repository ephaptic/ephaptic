import asyncio
import pytest
from ephaptic.ephaptic import Ephaptic, EphapticTarget, expose as global_expose
from ephaptic.decorators import META_KEY
from ephaptic.errors import ServiceError, EphapticError, RatelimitExceededException
import pydantic
from fastapi import FastAPI

def test_global_expose_picked_up():
    @global_expose
    def g_func():
        return 'global'
    
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    assert 'g_func' in eph._exposed_functions
    assert eph._exposed_functions['g_func']() == 'global'

def test_expose_metadata_storage():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose(rate_limit='5/m')
    def limited(): ...

    meta = getattr(limited, META_KEY)
    assert meta['rate_limit'] == (5, 60)

def test_expose_name():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose(name='new_name')
    def old_name(): return 'ok'

    assert 'new_name' in eph._exposed_functions
    assert eph._exposed_functions['new_name']() == 'ok'

def test_expose_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose
    def my_func():
        return "hello"

    assert "my_func" in eph._exposed_functions
    assert eph._exposed_functions["my_func"]() == "hello"

def test_event_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.event
    class MyEvent(pydantic.BaseModel):
        message: str

    assert "MyEvent" in eph._exposed_events
    assert eph._exposed_events["MyEvent"] == MyEvent

def test_identity_loader_with_from_app():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.identity_loader
    def my_loader(token):
        return "user123"

    assert eph._identity_loader is not None
    assert eph._identity_loader("some_token") == "user123"

def test_to_method():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    from ephaptic.utils import identity_key

    target = eph.to("user1", "user2")
    assert isinstance(target, EphapticTarget)
    assert target.user_ids == [identity_key("user1"), identity_key("user2")]

    target_list = eph.to(["user3", "user4"])
    assert target_list.user_ids == [identity_key("user3"), identity_key("user4")]

    from ephaptic.utils import UnsupportedIdentityError
    with pytest.raises(UnsupportedIdentityError):
        eph.to(("org", 5))


def test_include_binds_and_mounts():
    from ephaptic.ext.fastapi import Router

    app = FastAPI()
    eph = Ephaptic.from_app(app)
    router = Router(eph)

    @router.get('/ping')
    def ping() -> str:
        return 'pong'

    eph.include(router)

    assert router.ephaptic is eph
    # mounted as an HTTP route...
    assert any(getattr(r, 'path', None) == '/ping' for r in app.routes)
    # ...and exposed over RPC.
    assert 'ping' in eph._exposed_functions


def test_include_requires_an_app():
    eph = Ephaptic() # constructed directly, never attached to an app
    with pytest.raises(RuntimeError):
        eph.include(object())


def test_http_ratelimit_honours_ip_header():
    from fastapi.testclient import TestClient

    app = FastAPI()
    eph = Ephaptic.from_app(app, ip_header='X-Forwarded-For')
    router = eph.router()

    @router.get('/rl', limit='1/m')
    def rl() -> str:
        return 'ok'

    eph.include(router)
    client = TestClient(app)

    headers = {'X-Forwarded-For': '9.9.9.9'}
    assert client.get('/rl', headers=headers).status_code == 200
    assert client.get('/rl', headers=headers).status_code == 429

    assert client.get('/rl', headers={'X-Forwarded-For': '8.8.8.8'}).status_code == 200


def test_expose_rejects_unknown_option():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    with pytest.raises(TypeError):
        @eph.expose(requires_logon=True)
        def typo(): ...


def test_expose_requires_login_is_recorded():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.expose(requires_login=True)
    def guarded() -> str: return 'secret'

    assert getattr(guarded, META_KEY)['requires_login'] is True


def test_expose_all():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    eph.expose_all({'a': lambda: 1, 'b': lambda: 2})
    assert 'a' in eph._exposed_functions and 'b' in eph._exposed_functions


def test_ctx_accessors_false_outside_invocation():
    from ephaptic.ctx import is_http, is_rpc
    assert is_http() is False
    assert is_rpc() is False


def test_parse_limit_accepts_full_unit_names():
    from ephaptic.utils import parse_limit
    assert parse_limit('5/m') == (5, 60)
    assert parse_limit('100/hour') == (100, 3600)
    assert parse_limit('10 per 30s') == (10, 30)
    assert parse_limit('2/day') == (2, 86400)
    with pytest.raises(ValueError):
        parse_limit('5/fortnight')


def test_identity_key_is_injective():
    from ephaptic.utils import identity_key, UnsupportedIdentityError
    import uuid

    assert identity_key('7') != identity_key(7)
    assert identity_key(True) != identity_key(1)
    assert identity_key(False) != identity_key(0)
    assert identity_key('a') != identity_key('b')
    assert identity_key(0) and identity_key('')
    assert identity_key(uuid.uuid4())

    with pytest.raises(UnsupportedIdentityError):
        identity_key(object())


async def test_resolve_error_prefers_registered_handler_over_http_mapping():
    from fastapi import HTTPException
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    @eph.exception_handler(HTTPException)
    def handle(exc):
        return ServiceError('mapped', code='CUSTOM_HTTP')

    wire = await eph._resolve_error(HTTPException(status_code=404, detail='nope'))
    assert wire['code'] == 'CUSTOM_HTTP'


async def test_resolve_error_handler_returning_none_is_generic():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    class Boom(Exception): ...

    @eph.exception_handler(Boom)
    def handle(exc): return None

    wire = await eph._resolve_error(Boom())
    assert wire == {'code': 'INTERNAL', 'message': 'Internal server error.', 'data': None}


def test_router_derives_name_from_path_for_anonymous_handler():
    from ephaptic.ext.fastapi.router import derive_name

    assert derive_name('/users/{id}/posts') == 'users_posts'
    assert derive_name('/sum') == 'sum'
    assert derive_name('/a-b/c') == 'a_b_c'
    assert derive_name('/{id}') == ''


def test_router_raises_on_duplicate_rpc_name():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    router = eph.router()

    @router.get('/one')
    def dup() -> str: return 'a'

    with pytest.raises(ValueError):
        @router.get('/two', name='dup')
        def other() -> str: return 'b'


def test_router_preserves_the_application_reference():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    router = eph.router()

    @router.get('/thing')
    def thing() -> str: return 'ok'

    assert callable(thing)
    assert thing() == 'ok'


def test_router_lambda_uses_path_derivation():
    app = FastAPI()
    eph = Ephaptic.from_app(app)
    router = eph.router()

    # `lambda` is not a name, so the RPC name would come from the path
    router.get('/derived')(lambda: 'ok')
    assert 'derived' in eph._exposed_functions


def test_service_error_subclass_defaults():
    class NotFound(ServiceError):
        code = 'NOT_FOUND'
        message = 'Missing.'
        status_code = 404

    err = NotFound()
    assert err.code == 'NOT_FOUND'
    assert err.message == 'Missing.'
    assert err.status_code == 404
    assert err.to_wire() == {'code': 'NOT_FOUND', 'message': 'Missing.', 'data': None}

    override = NotFound('Custom message.', data={'id': 1})
    assert override.message == 'Custom message.'
    assert override.to_wire()['data'] == {'id': 1}


def test_service_error_dumps_pydantic_data():
    class Payload(pydantic.BaseModel):
        x: int

    err = ServiceError('oops', code='X', data=Payload(x=5))
    assert err.to_wire() == {'code': 'X', 'message': 'oops', 'data': {'x': 5}}


def test_ratelimit_exception_is_service_error():
    err = RatelimitExceededException('slow down', retry_after=7)
    assert isinstance(err, ServiceError)
    assert err.code == 'RATELIMIT'
    assert err.status_code == 429
    assert err.data == {'retry_after': 7}


def test_ephaptic_error_from_wire():
    err = EphapticError.from_wire({'code': 'X', 'message': 'm', 'data': {'a': 1}})
    assert err.code == 'X' and err.message == 'm' and err.data == {'a': 1}

    legacy = EphapticError.from_wire('just a string')
    assert legacy.code == 'ERROR' and legacy.message == 'just a string'


async def test_resolve_error_generic_hides_details():
    app = FastAPI()
    eph = Ephaptic.from_app(app) # debug defaults to False
    wire = await eph._resolve_error(ValueError('secret'))
    assert wire['code'] == 'INTERNAL'
    assert 'secret' not in wire['message']
    assert wire['data'] is None


async def test_resolve_error_debug_shows_details():
    app = FastAPI()
    eph = Ephaptic.from_app(app, debug=True)
    wire = await eph._resolve_error(ValueError('secret'))
    assert wire['code'] == 'INTERNAL'
    assert 'secret' in wire['message']


async def test_resolve_error_uses_registered_handler():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    class Boom(Exception): ...

    @eph.exception_handler(Boom)
    def handle(exc):
        return ServiceError('handled', code='BOOM', data={'ok': True})

    wire = await eph._resolve_error(Boom())
    assert wire == {'code': 'BOOM', 'message': 'handled', 'data': {'ok': True}}


def test_error_registration():
    app = FastAPI()
    eph = Ephaptic.from_app(app)

    class MyData(pydantic.BaseModel):
        reason: str

    @eph.error
    class Denied(ServiceError):
        code = 'DENIED'
        data: MyData

    assert 'DENIED' in eph._errors
    assert eph._errors['DENIED'] is Denied


async def test_websocket_transport_serialises_concurrent_sends():
    from ephaptic.transports.websocket import WebSocketTransport

    events = []

    class FakeWS:
        async def send(self, data):
            events.append(('enter', data))
            await asyncio.sleep(0.01)
            events.append(('exit', data))

    transport = WebSocketTransport(FakeWS())
    await asyncio.gather(transport.send(b'a'), transport.send(b'b'))

    assert [e[0] for e in events] == ['enter', 'exit', 'enter', 'exit']


def test_quart_adapter_selected():
    pytest.importorskip('quart')
    from quart import Quart

    app = Quart(__name__)
    eph = Ephaptic.from_app(app)
    assert eph is not None
    assert eph.manager is not None