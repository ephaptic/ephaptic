import pytest
import asyncio
import os
import httpx
import json

from ephaptic import connect
from ephaptic.errors import EphapticError

PORT = os.getenv('TEST_PORT', '8000')
SERVER_URL = f"ws://127.0.0.1:{PORT}/_ephaptic"
HTTP_SERVER_URL = f"http://127.0.0.1:{PORT}"

@pytest.mark.asyncio
async def test_rpc_echo():
    client = await connect(SERVER_URL, auth="user123")
    result = await client.echo(message="Hello, Ephaptic!")
    assert result == "Hello, Ephaptic!"


@pytest.mark.asyncio
async def test_rpc_add():
    client = await connect(SERVER_URL, auth="user123")
    result = await client.add(a=5, b=7)
    assert result == 12


@pytest.mark.asyncio
async def test_rpc_get_user_id():
    client = await connect(SERVER_URL, auth="user123")
    result = await client.get_user_id()
    assert result == "user123"

@pytest.mark.asyncio
async def test_pydantic_objects():
    client = await connect(SERVER_URL, auth="user123")
    result = await client.test_pydantic({ 'text': 'hi', 'num': 5 })
    assert result['text'] == 'hi' and result['num'] == 5


@pytest.mark.asyncio
async def test_event_emission():
    client = await connect(SERVER_URL, auth="user123")
    
    received_event_data = asyncio.Queue()

    def event_handler(message: str):
        received_event_data.put_nowait(message)

    client.on("MyEvent", event_handler)

    await client.emit_event(message="Integration test event")

    try:
        message = await asyncio.wait_for(received_event_data.get(), timeout=5)
        assert message == "Integration test event"
    except asyncio.TimeoutError:
        pytest.fail("Did not receive 'MyEvent' event within timeout.")
    finally:
        client.off("MyEvent", event_handler)
    

@pytest.mark.asyncio
async def test_typed_event_emission():
    client = await connect(SERVER_URL, auth="user123")
    
    received_event_data = asyncio.Queue()

    def event_handler(value: int):
        received_event_data.put_nowait({"value": value})
    client.on("MyTypedEvent", event_handler)

    await client.emit_typed_event(value=42)

    try:
        event_payload = await asyncio.wait_for(received_event_data.get(), timeout=5)
        assert event_payload == {"value": 42}
    except asyncio.TimeoutError:
        pytest.fail("Did not receive 'MyTypedEvent' event within timeout.")
    finally:
        client.off("MyTypedEvent", event_handler)

@pytest.mark.asyncio
async def test_rpc_stream_functions():
    client = await connect(SERVER_URL)

    stream = await client.async_generator()

    async for item in stream:
        assert isinstance(item, str)
        assert item.startswith('Message ')
    

@pytest.mark.asyncio
async def test_stream_mid_error_surfaces():
    client = await connect(SERVER_URL)

    stream = await client.failing_stream()

    received = []
    with pytest.raises(EphapticError) as exc_info:
        async for item in stream:
            received.append(item)

    assert received == ['first']
    assert exc_info.value.code == 'STREAM_FAILED'
    assert exc_info.value.data == {'after': 'first'}


@pytest.mark.asyncio
async def test_router_rpc_access():
    client = await connect(SERVER_URL, auth="user123")
    result = await client.r_echo(message="hello")
    assert result['is_rpc'] == True
    assert result['is_http'] == False
    assert result['active_user'] == 'user123'
    assert result['message'] == 'hello'

@pytest.mark.asyncio
async def test_custom_objects():
    client = await connect(SERVER_URL)
    result = await client.r_test_custom()

    assert result['text'] == 'Custom'
    assert result['num']  == 0
    assert result['default'] == 'DEFAULT'

    async with httpx.AsyncClient(base_url=HTTP_SERVER_URL) as client:
        resp = await client.get('/r_test_custom')
        assert resp.status_code == 200
        result = resp.json()

        # This is more of a FastAPI test than an Ephaptic test ngl

        assert result['text'] == 'Custom'
        assert result['num']  == 0
        assert result['default'] == 'DEFAULT'

@pytest.mark.asyncio
async def test_router_http_access():
    async with httpx.AsyncClient(base_url=HTTP_SERVER_URL) as client:
        resp = await client.get('/r_echo', params={'message': 'hello'})
        assert resp.status_code == 401

        resp = await client.get('/r_echo', params={'message': 'hello'}, headers={'Authorization': 'Bearer user123'})
        assert resp.status_code == 200
        result = resp.json()

        assert result['is_rpc'] == False
        assert result['is_http'] == True
        assert result['active_user'] == 'user123'
        assert result['message'] == 'hello'

@pytest.mark.asyncio
async def test_router_http_jsonl():
    async with httpx.AsyncClient(base_url=HTTP_SERVER_URL) as client:
        
        async with client.stream("GET", "/r_asyncgen") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/jsonl"

            received_lines = []
            async for line in resp.aiter_lines():
                if line.strip():
                    received_lines.append(json.loads(line))

            assert len(received_lines) == 2
            assert received_lines[0] == "Message A"
            assert received_lines[1] == "Message B"

        async with client.stream("GET", "/r_syncgen") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/jsonl"

            received_objects = []
            async for line in resp.aiter_lines():
                if line.strip():
                    received_objects.append(json.loads(line))

            assert len(received_objects) == 2
            
            assert received_objects[0]["text"] == "Message C"
            assert received_objects[0]["num"] == 0
            
            assert received_objects[1]["text"] == "Message D"
            assert received_objects[1]["num"] == 1

@pytest.mark.asyncio
async def test_service_error_over_rpc():
    client = await connect(SERVER_URL, auth="user123")
    with pytest.raises(EphapticError) as exc_info:
        await client.withdraw(amount=500)

    err = exc_info.value
    assert err.code == 'INSUFFICIENT_FUNDS'
    assert err.message == 'You do not have enough funds.'
    assert err.data == {'required': 500, 'available': 100}


@pytest.mark.asyncio
async def test_service_error_over_http():
    async with httpx.AsyncClient(base_url=HTTP_SERVER_URL) as client:
        resp = await client.get('/r_echo', params={'message': 'hi'})
        assert resp.status_code == 401
        body = resp.json()
        assert body['code'] == 'UNAUTHORIZED'


@pytest.mark.asyncio
async def test_unhandled_error_is_generic():
    client = await connect(SERVER_URL, auth="user123")
    with pytest.raises(EphapticError) as exc_info:
        await client.raise_unhandled()

    err = exc_info.value
    assert err.code == 'INTERNAL'
    # debug = False
    assert 'secret internal detail' not in err.message
    assert err.data is None


@pytest.mark.asyncio
async def test_http_exception_over_rpc():
    client = await connect(SERVER_URL, auth="user123")
    with pytest.raises(EphapticError) as exc_info:
        await client.raise_http()

    err = exc_info.value
    assert err.code == 'HTTP_404'
    assert err.message == 'Nope'


@pytest.mark.asyncio
async def test_ephaptic_exception_handler():
    client = await connect(SERVER_URL, auth="user123")
    with pytest.raises(EphapticError) as exc_info:
        await client.raise_custom()

    err = exc_info.value
    assert err.code == 'CUSTOM_HANDLED'
    assert err.data == {'handled': True}


@pytest.mark.asyncio
async def test_app_exception_handler_over_rpc():
    client = await connect(SERVER_URL, auth="user123")
    with pytest.raises(EphapticError) as exc_info:
        await client.raise_app_level()

    err = exc_info.value
    assert err.code == 'APP_LEVEL'
    assert err.message == 'from app handler'
    assert err.data == {'teapot': True}


@pytest.mark.asyncio
async def test_ratelimit_is_typed_error():
    client = await connect(SERVER_URL, auth="rl-user")
    await client.spam_me()
    with pytest.raises(EphapticError) as exc_info:
        await client.spam_me()

    err = exc_info.value
    assert err.code == 'RATELIMIT'
    assert isinstance(err.data, dict) and 'retry_after' in err.data


@pytest.mark.asyncio
async def test_expose_requires_login_rejects_anonymous():
    # @expose(requires_login=True)
    anon = await connect(SERVER_URL)
    with pytest.raises(EphapticError) as exc_info:
        await anon.rpc_secret()
    assert exc_info.value.code == 'UNAUTHORIZED'

    authed = await connect(SERVER_URL, auth="user123")
    assert await authed.rpc_secret() == 'rpc-secret'


@pytest.mark.asyncio
async def test_falsy_and_null_chunks_survive():
    client = await connect(SERVER_URL)
    received = []
    async for value in await client.falsy_stream():
        received.append(value)
    assert received == [0, None, 1]


@pytest.mark.asyncio
async def test_null_result_is_delivered():
    client = await connect(SERVER_URL)
    assert await client.returns_none() is None


@pytest.mark.asyncio
async def test_call_id_restarts_at_one_per_connection():
    client = await connect(SERVER_URL, auth="user123")
    await client.echo(message="a")
    assert client._call_id == 1


@pytest.mark.asyncio
async def test_call_escape_hatch_for_colliding_names():
    client = await connect(SERVER_URL, auth="user123")
    assert await client.call('echo', message='via call') == 'via call'


@pytest.mark.asyncio
async def test_disconnect_suppresses_reconnection():
    client = await connect(SERVER_URL, auth="user123")
    await client.echo(message="x")
    await client.disconnect()
    assert client.state == 'closed'
    with pytest.raises(EphapticError) as exc_info:
        await client.echo(message="y")
    assert exc_info.value.code == 'DISCONNECTED'


@pytest.mark.asyncio
async def test_router_functions_in_openapi():
    async with httpx.AsyncClient(base_url=HTTP_SERVER_URL) as client:
        resp = await client.get('/openapi.json')
        result = resp.json()

        assert 'paths' in result
        paths = result['paths']

        assert '/r_echo' in paths and 'get' in paths['/r_echo']
        r_echo = paths['/r_echo']['get']

        assert r_echo['parameters'][0]['name'] == 'message'
        assert r_echo['parameters'][0]['required'] == True
        assert r_echo['parameters'][0]['schema']['type'] == 'string'