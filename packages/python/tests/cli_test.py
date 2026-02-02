import pytest
from ephaptic.cli.__main__ import app, TS_resolve_type, KT_resolve_type, validate

def test_ts_resolver():
    # basic types
    assert TS_resolve_type({'type': 'string'}) == 'string'
    assert TS_resolve_type({'type': 'integer'}) == 'number'

    # array
    assert TS_resolve_type({'type': 'array', 'items': {'type': 'boolean'}}) == 'boolean[]'

    # Object
    obj = {
        'type': 'object',
        'properties': {'id': {'type': 'integer'}, 'name': {'type': 'string'}},
        'required': ['id']
    }
    res = TS_resolve_type(obj)
    assert 'id: number' in res
    assert 'name?: string' in res

def test_kt_resolver():
    # nullability?
    assert KT_resolve_type({'type': 'string'}) == 'String'
    
    # optional / union
    union = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
    assert KT_resolve_type(union) == 'String?'
    
    # array
    assert KT_resolve_type({'type': 'array', 'items': {'type': 'integer'}}) == 'List<Long>'

from typer.testing import CliRunner

runner = CliRunner()

fixture_path = 'packages.python.tests.fixtures.server:ephaptic'

def test_generate_ts():
    result = runner.invoke(app, ['generate', fixture_path, '-o', '-', '--lang', 'ts'])

    try: assert result.exit_code == 0, result.stdout + result.stderr
    except AssertionError: raise result.exception

    output = result.stdout

    assert "export interface MyEvent" in output
    assert "message: string;" in output

    assert "echo(message: string): Promise<string>;" in output
    assert "add(a: number, b: number): Promise<number>;" in output
    
    assert "MyEvent: MyEvent;" in output
    
    assert "queries: {" in output
    assert "echo(message: string): EphapticQuery" in output

def test_generate_kt():
    result = runner.invoke(app, ['generate', fixture_path, '-o', '-', '--lang', 'kt'])

    try: assert result.exit_code == 0, result.stdout + result.stderr
    except AssertionError: raise result.exception
    output = result.stdout

    assert "package com.example.app" in output

    assert "@JsonClass" in output
    assert "data class MyEvent(" in output
    assert "val message: String" in output

    assert "suspend fun echo(message: String): String" in output
    assert "suspend fun add(a: Long, b: Long): Long" in output

def test_generate_json():
    result = runner.invoke(app, ['generate', fixture_path, '-o', '-', '--lang', 'json'])

    try: assert result.exit_code == 0, result.stdout + result.stderr
    except AssertionError: raise result.exception
    output = result.stdout
    
    import json
    schema = json.loads(output)
    
    assert "methods" in schema
    assert "echo" in schema["methods"]
    assert "events" in schema
    assert "MyEvent" in schema["events"]