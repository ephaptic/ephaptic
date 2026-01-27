import pytest
import asyncio
import subprocess
import httpx
import os, sys

from ephaptic import connect

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
    
