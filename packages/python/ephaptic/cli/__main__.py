import sys, os, json, inspect, importlib, typing, typer, subprocess as sp

from pathlib import Path
from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

from ephaptic import Ephaptic

app = typer.Typer(help="Ephaptic CLI tool.")

def load_ephaptic(import_name: str) -> Ephaptic:
    try:
        from dotenv import load_dotenv; load_dotenv()
    except: ...

    sys.path.insert(0, os.getcwd())

    if ":" not in import_name:
        typer.secho(f"Warning: Import name did not specify client name. Defaulting to `client`.", fg=typer.colors.YELLOW)
        import_name += ":client" # default: expect client to be named `client` inside the file

    module_name, var_name = import_name.split(":", 1)

    try:
        typer.secho(f"Attempting to import `{var_name}` from `{module_name}`...")
        module = importlib.import_module(module_name)
    except ImportError as e:
        typer.secho(f"Error: Can't import '{module_name}'.\n{e}", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    try:
        instance = getattr(module, var_name)
    except AttributeError:
        typer.secho(f"Error: Variable '{var_name}' not found in module '{module_name}'.", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    if not isinstance(instance, Ephaptic):
        typer.secho(f"Error: '{var_name}' is not an Ephaptic client. It is type: {type(instance)}", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    return instance

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

@app.command()
def generate(
    client: str = typer.Argument('client:client', help="The import string for the Ephaptic client."),
    output: Path = typer.Option('schema.json', '--output', '-o', help="Output path for the JSON schema."),
    watch: bool = typer.Option(False, '--watch', '-w', help="Watch for changes in `.py` files and regenerate schema file automatically."),
):
    if watch:
        import watchfiles
        
        cwd = os.getcwd()
        typer.secho(f"Watching for changes ({cwd})...",  fg=typer.colors.GREEN)

        run_subprocess()

        for changes in watchfiles.watch(cwd):
            if any(f.endswith('.py') for _, f in changes):
                typer.secho("Detected changes, regenerating...")
                run_subprocess()

    ephaptic = load_ephaptic(client)

    schema_output = {
        "methods": {},
        "events": {},
        "definitions": {},
    }

    typer.secho(f"Found {len(ephaptic._exposed_functions)} functions.", fg=typer.colors.GREEN)

    for name, func in ephaptic._exposed_functions.items():
        typer.secho(f"  - {name}")

        hints = typing.get_type_hints(func)
        sig = inspect.signature(func)

        method_schema = {
            "args": {},
            "return": None,
            "required": [],
        }

        for param_name, param in sig.parameters.items():
            hint = hints.get(param_name, typing.Any)
            adapter = TypeAdapter(hint)

            method_schema["args"][param_name] = create_schema(
                adapter,
                schema_output["definitions"],
            )

            if param.default == inspect.Parameter.empty:
                method_schema["required"].append(param_name)
            else:
                method_schema["args"][param_name]["default"] = str(param.default)

            

        return_hint = hints.get("return", typing.Any)
        if return_hint is not type(None):
            adapter = TypeAdapter(return_hint)
            method_schema["return"] = create_schema(
                adapter,
                schema_output["definitions"],
            )

        schema_output["methods"][name] = method_schema

    typer.secho(f"Found {len(ephaptic._exposed_events)} events.", fg=typer.colors.GREEN)

    for name, model in ephaptic._exposed_events.items():
        typer.secho(f"  - {name}")
        adapter = TypeAdapter(model)

        schema_output["events"][name] = create_schema(
            adapter,
            schema_output["definitions"],
        )

    new = json.dumps(schema_output, indent=2)

    if output.exists():
        old = output.read_text()
        if old == new:
            return

    with open(output, "w") as f:
        f.write(new)

    typer.secho(f"Schema generated to `{output}`.", fg=typer.colors.GREEN, bold=True)

if __name__ == "__main__":
    app()