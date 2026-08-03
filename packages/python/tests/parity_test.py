"""
Python client X TS server :D
"""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ephaptic import connect
from ephaptic.errors import EphapticError

REPO = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO / 'packages' / 'js' / 'server'
HARNESS = SERVER_DIR / 'src' / 'tests' / 'parity-server.mjs'
BUILT = SERVER_DIR / 'dist' / 'index.js'

pytestmark = [
    pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed'),
    pytest.mark.skipif(not BUILT.exists(), reason="run 'npm run build' in packages/js/server first"),
    pytest.mark.skipif(not HARNESS.exists(), reason='parity harness is absent'),
]


@pytest.fixture(scope='module')
def ts_server():
    proc = subprocess.Popen(
        [shutil.which('node'), str(HARNESS)],
        cwd=str(SERVER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, 'PARITY_PORT': '0'},
    )
    try:
        line = proc.stdout.readline()
        if not line.startswith('READY'):
            proc.kill()
            remainder = proc.stdout.read()
            pytest.fail(f'parity server did not start: {line}{remainder}')
        port = int(line.split()[1])
        yield f'ws://127.0.0.1:{port}/_ephaptic'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
async def client(ts_server):
    c = await connect(ts_server, auth={'token': 'user-1'})
    try:
        yield c
    finally:
        await c.disconnect()


async def test_scalar_and_container_results_cross(client):
    assert await client.add(2, 3) == 5
    assert await client.echo('hi') == 'hi'
    assert await client.echo({'a': [1, 2], 'b': None}) == {'a': [1, 2], 'b': None}


async def test_a_null_result_is_a_value_not_an_error(client):
    assert await client.returns_null() is None


async def test_identity_crosses_the_init_frame(client):
    assert await client.whoami() == 'user-1'


async def test_named_values_are_applied_by_name(client):
    # these kwargs should be parsed correctly and put first before second
    assert await client.named(second='B', first='A') == 'A|B'


async def test_a_typed_error_crosses_with_its_code_and_data(client):
    with pytest.raises(EphapticError) as excinfo:
        await client.boom()
    assert excinfo.value.code == 'INSUFFICIENT_FUNDS'
    assert excinfo.value.data == {'available': 3}


async def test_an_untyped_error_crosses_masked(client):
    with pytest.raises(EphapticError) as excinfo:
        await client.kaboom()
    assert excinfo.value.code == 'INTERNAL'
    assert 'sk-proj' not in excinfo.value.message


async def test_requires_login_admits_an_authenticated_caller(client):
    assert await client.secret() == 'sk-proj-1234'


async def test_requires_login_refuses_an_anonymous_caller(ts_server):
    anon = await connect(ts_server)
    try:
        with pytest.raises(EphapticError) as excinfo:
            await anon.secret()
        assert excinfo.value.code == 'UNAUTHORIZED'
    finally:
        await anon.disconnect()


async def test_a_stream_yields_every_chunk_in_order(client):
    assert [v async for v in await client.countdown(4)] == [4, 3, 2, 1]


async def test_a_falsy_chunk_is_not_treated_as_completion(client):
    assert [v async for v in await client.falsy_stream()] == [0, None, 1]


async def test_a_mid_stream_error_reaches_the_consumer(client):
    received = []
    with pytest.raises(EphapticError) as excinfo:
        async for value in await client.badstream():
            received.append(value)
    assert excinfo.value.code == 'INSUFFICIENT_FUNDS'
    assert received == [1]


async def test_an_event_crosses_with_its_payload(client):
    seen = asyncio.get_running_loop().create_future()

    def record(**kwargs):
        if not seen.done():
            seen.set_result(kwargs)

    client.on('pong', record)
    assert await client.ping() == 'sent'
    assert await asyncio.wait_for(seen, timeout=10) == {'ok': True}