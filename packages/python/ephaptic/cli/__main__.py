import sys, os, subprocess as sp
import hashlib, json, re
import inspect, importlib, typing
from pathlib import Path

import typer
import docstring_parser

from pydantic import TypeAdapter

from ephaptic import Ephaptic
from ephaptic.decorators import META_KEY

from typing import Any, Dict, List

app = typer.Typer(help="Ephaptic CLI tool.")

IDENTIFIER_REGEX = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

LOG: List[str] = []

def log(*data):
    global LOG
    LOG.append(' '.join(data))

def clear_log():
    global LOG
    LOG = []

def key_name(key: str) -> str:
    if IDENTIFIER_REGEX.match(key): return key
    else: return json.dumps(key)

def validate(name: str) -> str:
    if not IDENTIFIER_REGEX.match(name):
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # digest to stop a.b and a_b being same. it makes it ugly but thats YOUR FAULT FOR HAVING WEIRD FUNCTION NAMES
        digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:6]
        safe = f'{safe}_{digest}'
        log(typer.style(f"[warning] '{name}' is not a valid identifier. sanitizing to '{safe}'", fg=typer.colors.YELLOW))
        return safe
    return name

# because we want kotlin to still compile
KOTLIN_KEYWORDS = frozenset({
    'as', 'break', 'class', 'continue', 'do', 'else', 'false', 'for', 'fun', 'if',
    'in', 'interface', 'is', 'null', 'object', 'package', 'return', 'super',
    'this', 'throw', 'true', 'try', 'typealias', 'typeof', 'val', 'var', 'when',
    'while',
})

def kt_literal(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')
    return f'"{escaped}"'

def kt_name(name: str) -> str:
    safe = validate(name)
    if safe and safe[0].isdigit(): safe = f'n{safe}'
    safe = safe.replace('$', '_')
    return f'`{safe}`' if safe in KOTLIN_KEYWORDS else safe

# syntax-highlighted function signatures

class Colours:
    Header = typer.colors.BLUE
    Async = typer.colors.MAGENTA
    Def = typer.colors.BLUE
    Fn = typer.colors.BRIGHT_YELLOW
    Param = typer.colors.CYAN
    Type = typer.colors.GREEN
    Default = typer.colors.BRIGHT_BLACK
    Dash = typer.colors.BRIGHT_BLACK

def _dash() -> str: return typer.style('—', fg=Colours.Dash)

def _header(text: str) -> str: return typer.style(text, fg=Colours.Header, bold=True)

def format_type(tp) -> str:
    if tp is inspect.Parameter.empty or tp is inspect.Signature.empty: return ''
    if tp is None or tp is type(None): return 'None'
    if tp is typing.Any: return 'Any'

    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if origin is None:
        name = getattr(tp, '__name__', None)
        return name if name else str(tp).replace('typing.', '')

    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return f'Optional[{format_type(non_none[0])}]'
        return ' | '.join(format_type(a) for a in args)

    origin_name = getattr(origin, '__name__', None) or getattr(origin, '_name', None) or str(origin).replace('typing.', '')

    if origin_name in ('Generator', 'AsyncGenerator', 'Iterable', 'AsyncIterable', 'Iterator', 'AsyncIterator'):
        inner = format_type(args[0]) if args else ''
        return f'{origin_name}[{inner}]'

    if args: return f"{origin_name}[{', '.join(format_type(a) for a in args)}]"

    return origin_name

def render_signature(name: str, sig: inspect.Signature, hints: dict, is_async: bool, return_hint) -> str:
    out = ''
    if is_async: out += typer.style('async ', fg=Colours.Async)
    out += typer.style('def ', fg=Colours.Def)
    out += typer.style(name, fg=Colours.Fn)

    params = []
    for param_name, param in sig.parameters.items():
        piece = typer.style(param_name, fg=Colours.Param)
        type_str = format_type(hints.get(param_name, inspect.Parameter.empty))
        if type_str: piece += ': ' + typer.style(type_str, fg=Colours.Type)
        if param.default is not inspect.Parameter.empty: piece += typer.style(f' = {param.default!r}', fg=Colours.Default)
        params.append(piece)

    out += '(' + ', '.join(params) + ')'

    return_str = format_type(return_hint)
    if return_str: out += ' -> ' + typer.style(return_str, fg=Colours.Type)

    return out

def parse_docstring(func):
    doc = inspect.getdoc(func)
    if not doc: return None, {}, None

    try: parsed = docstring_parser.parse(doc)
    except Exception: return None, {}, None

    parts = [p for p in (parsed.short_description, parsed.long_description) if p]
    description = '\n\n'.join(parts) or None

    param_descriptions = {
        param.arg_name: ' '.join(param.description.split())
        for param in parsed.params if param.description
    }

    return_description = None
    if parsed.returns and parsed.returns.description: return_description = ' '.join(parsed.returns.description.split())

    return description, param_descriptions, return_description

def _doc_comment(method_data: dict, indent: str, returns_tag: str) -> List[str]:
    description = method_data.get('description')
    return_description = method_data.get('return_description')
    param_docs = [
        (name, schema.get('description'))
        for name, schema in method_data.get('args', {}).items()
        if schema.get('description')
    ]

    if not (description or return_description or param_docs): return []

    def clean(text: str) -> str: return text.replace('*/', '* /')

    lines = [f'{indent}/**']

    if description:
        for line in clean(description).splitlines(): lines.append(f'{indent} * {line}'.rstrip())

    if param_docs:
        if description: lines.append(f'{indent} *')
        for name, desc in param_docs: lines.append(f'{indent} * @param {validate(name)} {clean(desc)}')

    if return_description: lines.append(f'{indent} * {returns_tag} {clean(return_description)}')

    lines.append(f'{indent} */')

    return lines

def load_schema(input_path: str | Path):
    return json.loads(Path(input_path).read_text())
    # errors can be handled by typer

def load_ephaptic(import_name: str) -> Ephaptic:
    try:
        from dotenv import load_dotenv; load_dotenv()
    except: ...

    sys.path.insert(0, os.getcwd())

    if ":" not in import_name:
        log(typer.style(f"[WARN] Import name did not specify client name. Defaulting to `client`.", fg=typer.colors.YELLOW))
        import_name += ":client" # default: expect client to be named `client` inside the file

    module_name, var_name = import_name.split(":", 1)

    try:
        log(typer.style(f"Attempting to import `{var_name}` from `{module_name}`..."))
        module = importlib.import_module(module_name)
    except ImportError as e:
        typer.echo(typer.style(f"[ERROR] Can't import '{module_name}'.\n{e}", fg=typer.colors.RED))
        raise typer.Exit(1)
    
    try:
        instance = getattr(module, var_name)
    except AttributeError:
        typer.echo(typer.style(f"[ERROR] Variable '{var_name}' not found in module '{module_name}'.", fg=typer.colors.RED))
        raise typer.Exit(1)
    
    if not isinstance(instance, Ephaptic):
        typer.echo(typer.style(f"[ERROR] '{var_name}' is not an Ephaptic client. It is type: {type(instance)}", fg=typer.colors.RED))
        raise typer.Exit(1)
    
    return instance

def TS_resolve_type(schema: Dict[str, Any]) -> str:
    if not schema: return 'any'
    if schema.get('const'): return json.dumps(schema['const'])
    if schema.get('$ref'): return validate(schema['$ref'].split('/').pop() or 'any')
    if schema.get('enum'): return ' | '.join([json.dumps(val) for val in schema['enum']])
    if schema.get('anyOf'):
        seen = []
        for s in schema['anyOf']:
            resolved = TS_resolve_type(s)
            if resolved not in seen: seen.append(resolved)
        return ' | '.join(seen)
    if schema.get('type') == 'array': return f"({TS_resolve_type(schema['items']) if schema.get('items') else 'any'})[]"
    if schema.get('type') in ('integer', 'number'): return 'number'
    if schema.get('type') == 'boolean': return 'boolean'
    if schema.get('type') == 'string': return 'string'
    if schema.get('type') == 'null': return 'null'

    if schema.get('type') == 'object':
        if not schema.get('properties'): return 'Record<string, any>'
        props = [
            f"{key_name(key)}{'' if key in schema.get('required', []) else '?'}: {TS_resolve_type(prop_schema)}"
            for key, prop_schema in schema['properties'].items()
        ]
        return '{ ' + '; '.join(props) + ' }'
    
    return 'any'

def TS_generate(data: dict):
    lines: List[str] = []

    lines.extend([
        '/**',
        ' * Auto-generated by ephaptic',
        ' * Do not edit this file manually.',
        ' * */',
        '',
        'import { type EphapticClientBase } from "@ephaptic/client";',
        '',
        'export type EphapticQuery<TArgs extends any[], TReturn> = { queryKey: [string, ...TArgs]; queryFn: () => Promise<TReturn>; };',
        '',
    ])

    for name, schema in data.get('definitions', {}).items():
        name = validate(name)
        if schema['type'] == 'object':
            lines.append(f'export interface {name} {{')
            lines.extend([
                f"  {validate(prop_name)}{'' if prop_name in schema.get('required', []) else '?'}: {TS_resolve_type(prop_schema)};"
                for prop_name, prop_schema in schema['properties'].items()
            ])
            lines.append('}')
            lines.append('')
        else:
            lines.append(f'export type {name} = {TS_resolve_type(schema)};')
            lines.append('')

    lines.append('export interface EphapticEvents {')
    lines.extend([
        f" {validate(event_name)}: {TS_resolve_type(event_schema)};"
        for event_name, event_schema in data.get('events', {}).items()
    ])
    lines.append('}')
    lines.append('')

    lines.append('export interface EphapticErrors {')
    lines.extend([
        f"  {key_name(code)}: {TS_resolve_type(err['data']) if err.get('data') else 'null'};"
        for code, err in data.get('errors', {}).items()
    ])
    lines.append('}')
    lines.append('')

    lines.append('export interface EphapticService extends EphapticClientBase {')
    lines.append('')

    for method_name, method_data in data.get('methods', {}).items():
        args: List[str] = []

        args.extend([
            f"{validate(arg_name)}{'' if arg_name in method_data.get('required', []) else '?'}: {TS_resolve_type(arg_schema)}"
            for arg_name, arg_schema in method_data.get('args', {}).items()
        ])
        
        return_type = TS_resolve_type(method_data['return']) if method_data.get('return') else 'void'

        stream = method_data.get('stream', False)
        if stream:
            return_type = f'AsyncIterableIterator<{return_type}>'

        lines.extend(_doc_comment(method_data, '  ', '@returns'))
        lines.append(f"  {validate(method_name)}({', '.join(args)}): Promise<{return_type}>;")

    lines.append('')
    lines.append('  queries: {')

    for method_name, method_data in data.get('methods', {}).items():
        args: List[str] = []

        args.extend([
            f"{validate(arg_name)}{'' if arg_name in method_data.get('required', []) else '?'}: {TS_resolve_type(arg_schema)}"
            for arg_name, arg_schema in method_data.get('args', {}).items()
        ])

        arg_types = [TS_resolve_type(arg_schema) for arg_schema in method_data.get('args', {}).values()]
        
        return_type = TS_resolve_type(method_data['return']) if method_data.get('return') else 'void'

        lines.append(f"     {validate(method_name)}({', '.join(args)}): EphapticQuery<[{', '.join(arg_types)}], {return_type}>;")

    lines.append('  };')

    lines.append('')

    lines.append('    on<K extends keyof EphapticEvents>(event: K, callback: (data: EphapticEvents[K]) => void): void;')
    lines.append('    off<K extends keyof EphapticEvents>(event: K, callback: (data: EphapticEvents[K]) => void): void;')
    lines.append('    once<K extends keyof EphapticEvents>(event: K, callback: (data: EphapticEvents[K]) => void): void;')

    lines.append('}');
    lines.append('');

    lines.extend([
        '/**',
        ' * Usage:',
        ' * import { connect } from "@ephaptic/client";',
        ' * import { type EphapticService } from "./ephaptic";',
        ' * ',
        ' * const client = connect(...) as unknown as EphapticService;',
        ' */',
    ])

    return lines

def KT_resolve_type(schema: Dict[str, Any]) -> str:
    if not schema: return 'Any?'
    if schema.get('const'):
        val = schema['const']
        if isinstance(val, bool): return 'Boolean' # bool is a subclass of int, so check it first
        if isinstance(val, str): return 'String'
        if isinstance(val, int): return 'Long'
        if isinstance(val, float): return 'Double'
    if schema.get('$ref'): return validate(schema['$ref'].split('/').pop() or 'Any')
    if schema.get('enum') and len(schema['enum']) > 0:
        first = schema['enum'][0]
        if isinstance(first, bool): return 'Boolean'
        if isinstance(first, str): return 'String'
        if isinstance(first, int): return 'Long' # matches how plain integers resolve below
        if isinstance(first, float): return 'Double'
        return 'Any?'
    if schema.get('anyOf'):
        nonNull = [t for t in schema['anyOf'] if t.get('type') != 'null']
        if len(nonNull) == 1:
            type = KT_resolve_type(nonNull[0])
            if not type.endswith('?'): type += '?'
            return type
        return 'Any?'
    if schema.get('type') == 'array': return f"List<{KT_resolve_type(schema['items']) if schema.get('items') else 'Any?'}>"
    if schema.get('type') == 'integer': return 'Long'
    if schema.get('type') == 'number': return 'Double'
    if schema.get('type') == 'boolean': return 'Boolean'
    if schema.get('type') == 'string': return 'String'
    if schema.get('type') == 'null': return 'Any?'
    if schema.get('type') == 'object': return 'Map<String, Any?>'
    return 'Any?'

def KT_generate(data: dict, package_name: str):
    lines: List[str] = []

    lines.extend([
        '/**',
        ' * Auto-generated by ephaptic',
        ' * Do not edit this file manually.',
        ' * */',
        '',
        f'package {package_name}',
        '',
        'import com.squareup.moshi.Json',
        'import com.squareup.moshi.JsonClass',
        'import com.ephaptic.android.EphapticClient',
        'import com.ephaptic.android.EphapticException',
        '',
    ])

    for name, schema in data.get('definitions', {}).items():
        name = validate(name)
        if schema.get('type') == 'object':
            properties = schema.get('properties') or {}
            if not properties:
                lines.append(f'class {name}')
                lines.append('')
                continue

            lines.append('@JsonClass(generateAdapter = true)')
            lines.append(f'data class {name}(')

            for prop_name, prop_schema in properties.items():
                kt_type = KT_resolve_type(prop_schema)
                
                required = prop_name in schema.get('required', [])
                explicit_null = prop_schema.get('type') == 'null'
                union_null = any(t.get('type') == 'null' for t in prop_schema.get('anyOf', []))
                
                nullable = not required or explicit_null or union_null               
                if nullable and not kt_type.endswith('?'): kt_type += '?'

                field = kt_name(prop_name)
                bare = field.strip('`')
                annotation = f'@Json(name = {kt_literal(prop_name)}) ' if bare != prop_name else ''
                lines.append(f"  {annotation}val {field}: {kt_type}{' = null' if nullable else ''},")
                
            lines.append(')')
            lines.append('')
        else:
            lines.append(f'typealias {name} = {KT_resolve_type(schema)}')
            lines.append('')

    events = data.get('events', {})
    if events:
        lines.append('sealed class EphapticEvent {')
        lines.extend([
            f"  data class {kt_name(event_name)}(val data: {KT_resolve_type(event_schema)}) : EphapticEvent()"
            for event_name, event_schema in events.items()
        ])
        lines.append('}')
    else:
        lines.append('sealed class EphapticEvent')
    lines.append('')

    lines.append('class EphapticService(private val client: EphapticClient) {')
    lines.append('')

    for method_name, method_data in data.get('methods', {}).items():
        args: List[str] = []
        params: List[str] = []

        for arg_name, arg_schema in method_data.get('args', {}).items():
            is_req = arg_name in method_data.get('required', [])
            kt_type = KT_resolve_type(arg_schema)

            if not is_req and not kt_type.endswith('?'): kt_type += '?'

            args.append(f"{kt_name(arg_name)}: {kt_type}")
            params.append(kt_name(arg_name))

        return_type = KT_resolve_type(method_data['return']) if method_data.get('return') else 'Any?'

        stream = method_data.get('stream', False)

        lines.extend(_doc_comment(method_data, ' ', '@return'))

        if stream:
            lines.append(f" fun {kt_name(method_name)}({', '.join(args)}): kotlinx.coroutines.flow.Flow<{return_type}> {{")
            call_args = ''.join(f', {p}' for p in params)
            lines.append(f'      return client.stream<{return_type}>({kt_literal(method_name)}{call_args})')
            lines.append('  }')
        else:
            lines.append(f" suspend fun {kt_name(method_name)}({', '.join(args)}): {return_type} {{")
            call_args = ''.join(f', {p}' for p in params)
            lines.append(f'      return client.request<{return_type}>({kt_literal(method_name)}{call_args})')
            lines.append('  }')
        lines.append('')

    lines.append('}')
    lines.append('')

    errors = data.get('errors', {})
    if errors:
        lines.append('object EphapticErrors {')
        for code, err in errors.items():
            data_type = KT_resolve_type(err['data']) if err.get('data') else 'Any?'
            lines.append(f'  /** data: {data_type} */')
            lines.append(f'  const val {kt_name(code)} = {kt_literal(code)}')
        lines.append('}')
        lines.append('')

    return lines

def create_schema(adapter: TypeAdapter, definitions: dict) -> dict:
    schema = adapter.json_schema(ref_template='#/definitions/{model}')

    if '$defs' in schema:
        definitions.update(schema.pop('$defs'))

    if schema.get('type') == 'object' and 'title' in schema:
        model = schema['title']
        definitions[model] = schema
        return { '$ref': f'#/definitions/{model}' }
    
    return schema

def run_subprocess():
    cmd = [sys.executable]
    cmd += [arg for arg in sys.argv if arg not in {'--watch', '-w'}]
    sp.run(cmd)

def calculate_language(lang: str, output: Path):
    if lang is None:
        if not output or str(output) == '-': raise ValueError("You must specify a language or an output path.")
        lang = os.path.splitext(output)[-1]
        lang = lang.removeprefix('.')
    
    map = {
        'kotlin': 'kt',
        'typescript': 'ts',
    }

    if lang in map: lang = map[lang]

    if output is None:
        match lang:
            case 'ts': output = Path('ephaptic.d.ts')
            case 'kt': output = Path('Ephaptic.kt')
            case _: output = Path('schema.json')

    return lang, output

class NothingToChange(Exception): ...

def generate_output(lang, schema_output, package_name, output: Path):
    content = None
    match lang:
        case 'json': content = json.dumps(schema_output, indent=2)
        case 'ts': content = '\n'.join(TS_generate(schema_output))
        case 'kt': content = '\n'.join(KT_generate(schema_output, package_name))

    if str(output) == '-':
        print(content)
        for line in LOG:
            typer.echo(line, err=True) # so you can still see the logs in your terminal but they won't be piped to wherever
        return

    if output.exists():
        if output.read_text() == content:
            return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)

    for line in LOG:
        typer.echo(line)

    clear_log()

    typer.secho(f"Schema generated to `{output}`.", fg=typer.colors.GREEN, bold=True)


@app.command()
def generate(
    source: str = typer.Argument('schema.json', help="Either the import string for the Ephaptic client. (e.g. `app:client`) or a path to an existing schema file (e.g. `schema.json`)."),
    output: Path = typer.Option(None, '--output', '-o', help="Output path for the generated file (default: schema.json / ephaptic.d.ts / Ephaptic.kt)."),
    watch: bool = typer.Option(False, '--watch', '-w', help="Watch for changes in `.py` files and regenerate schema file automatically."),
    lang: str = typer.Option(None, '--lang', '-l', help="Output language ('json', 'kotlin', 'kt', 'typescript', 'ts') (default: autodetected from output path)"),
    package_name: str = typer.Option('com.example.app', '--package-name', '-p', help="Package name (required for Kotlin)")
):
    lang, output = calculate_language(lang, output)

    is_schema = Path(source).exists()
    if is_schema:
        source = Path(source)

    if watch:
        import watchfiles
        
        cwd = os.getcwd()
        typer.secho(f"Watching for changes ({cwd})...",  fg=typer.colors.GREEN)

        run_subprocess()

        for changes in watchfiles.watch(source if is_schema else cwd):
            if (is_schema and any(Path(f) == source for _, f in changes)) or (any(f.endswith('.py') for _, f in changes)):
                typer.secho("Detected changes, regenerating...")
                run_subprocess()

        return

    if is_schema:
        schema_output = json.loads(source.read_text())
    else:
        ephaptic = load_ephaptic(source)

        schema_output = {
            "methods": {},
            "events": {},
            "errors": {},
            "definitions": {},
        }

        if ephaptic._exposed_functions: log(_header("Functions"))

        for name, func in ephaptic._exposed_functions.items():
            meta = getattr(func, META_KEY, {})

            hints = meta.get('hints') or typing.get_type_hints(func)
            sig = meta.get('sig') or inspect.signature(func)

            is_async = inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func)
            display_return = meta.get('response_model') or hints.get('return', inspect.Signature.empty)
            log(f"  {_dash()} {render_signature(name, sig, hints, is_async, display_return)}")

            description, param_descriptions, return_description = parse_docstring(func)

            method_schema = {
                "args": {},
                "return": None,
                "required": [],
            }

            if description: method_schema["description"] = description
            if return_description: method_schema["return_description"] = return_description

            for param_name, param in sig.parameters.items():
                hint = hints.get(param_name, typing.Any)
                adapter = TypeAdapter(hint)

                method_schema["args"][param_name] = create_schema(
                    adapter,
                    schema_output["definitions"],
                )

                if param_name in param_descriptions:
                    method_schema["args"][param_name]["description"] = param_descriptions[param_name]

                if param.default == inspect.Parameter.empty:
                    method_schema["required"].append(param_name)
                else:
                    method_schema["args"][param_name]["default"] = str(param.default)

            return_hint = meta.get('response_model') or hints.get("return", typing.Any)
        
            stream = False
            origin = typing.get_origin(return_hint)
            origin_name = getattr(origin, '__name__', '')
            if origin in (typing.AsyncGenerator, typing.Generator, typing.AsyncIterable, typing.Iterable) or origin_name in ('AsyncGenerator', 'Generator', 'AsyncIterable', 'Iterable'):
                stream = True
                type_ = typing.get_args(return_hint)
                return_hint = type_[0] if type_ else typing.Any

            method_schema['stream'] = stream

            if return_hint and return_hint is not type(None) and return_hint is not typing.Any:
                adapter = TypeAdapter(return_hint)
                method_schema["return"] = create_schema(
                    adapter,
                    schema_output["definitions"],
                )

            schema_output["methods"][name] = method_schema

        if ephaptic._exposed_events: log(_header("Events"))

        for name, model in ephaptic._exposed_events.items():
            log(f"  {_dash()} {typer.style(name, fg=Colours.Fn)}")
            adapter = TypeAdapter(model)

            schema_output["events"][name] = create_schema(
                adapter,
                schema_output["definitions"],
            )

        if getattr(ephaptic, '_errors', {}):
            log(_header("Errors"))

        for code, cls in getattr(ephaptic, '_errors', {}).items():
            log(f"  {_dash()} {typer.style(code, fg=Colours.Fn)}")

            entry = {
                "code": getattr(cls, 'code', code),
                "message": getattr(cls, 'message', ''),
                "status_code": getattr(cls, 'status_code', 400),
                "data": None,
            }

            try:
                data_type = typing.get_type_hints(cls).get('data')
            except Exception:
                data_type = None
            if data_type is not None and data_type is not type(None) and data_type is not typing.Any:
                entry["data"] = create_schema(
                    TypeAdapter(data_type),
                    schema_output["definitions"],
                )

            schema_output["errors"][code] = entry

    generate_output(lang, schema_output, package_name, output)

click = typer.main.get_command(app)

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, version: bool = typer.Option(False, "--version", help="Show version and exit")):
    if version:
        from importlib.metadata import version as pkg_version
        typer.echo(pkg_version(__package__.split('.')[0]))
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        ctx.invoke(generate)

if __name__ == "__main__":
    app()