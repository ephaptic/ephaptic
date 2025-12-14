import sys, os, json, inspect, importlib, typing, typer

from pathlib import Path
from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

from ephaptic import Ephaptic

app = typer.Typer(help="Ephaptic CLI tool.")

def load_ephaptic(import_name: str) -> Ephaptic:
    sys.path.insert(0, os.getcwd())

    if ":" not in import_name:
        typer.secho(f"Warning: Import name did not specify app name. Defaulting to `app`.", fg=typer.colors.YELLOW)
        import_name += ":app" # default: expect app to be named `app` inside the file

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
        typer.secho(f"Error: '{var_name}' is not an Ephaptic instance. It is type: {type(instance)}", fg=typer.colors.RED)
        raise typer.Exit(1)
    
    return instance

@app.command()
def generate(
    app: str = typer.Argument('app:app', help="The import string. (Default: `app:app`)"),
    output: Path = typer.Option('schema.json', '--output', '-o', help="Output path for the JSON schema.")
):
    ephaptic = load_ephaptic(app)

    typer.secho(f"Found {len(ephaptic._exposed_functions)} functions.", fg=typer.colors.GREEN)

    schema_output = {
        "methods": {},
        "definitions": {},
    }

    collected = []

    for name, func in ephaptic._exposed_functions.items():
        typer.secho(f"  - {name}")

        hints = typing.get_type_hints(func)
        sig = inspect.signature(func)

        method_schema = {
            "args": {},
            "return": None
        }

        for name in sig.parameters:
            hint = hints.get(name, typing.Any)
            adapter = TypeAdapter(hint)
            schema = adapter.json_schema(ref_template='#/definitions/{model}')

            if '$defs' in schema:
                schema_output["definitions"].update(schema.pop("$defs"))

            method_schema["args"][name] = schema

        return_hint = hints.get("return", typing.Any)
        if return_hint is not type(None):
            adapter = TypeAdapter(return_hint)
            schema = adapter.json_schema(ref_template='#/definitions/{model}')

            if '$defs' in schema:
                schema_output["definitions"].update(schema.pop("$defs"))

        schema_output["methods"][name] = method_schema

    with open(output, "w") as f:
        json.dump(schema_output, f, indent=2)

    typer.secho("Schema generated to `{output}`", fg=typer.colors.GREEN, bold=True)

if __name__ == "__main__":
    app()