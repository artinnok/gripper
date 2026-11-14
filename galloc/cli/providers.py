import json

import click

from galloc.settings import Settings


@click.command("providers")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def providers_command(settings: Settings, output: str) -> None:
    """List providers and configuration status."""

    configured = settings.runpod_api_key is not None and bool(settings.runpod_api_key.get_secret_value().strip())
    if output == "json":
        click.echo(json.dumps([{"name": "runpod", "configured": configured}], indent=2))
        return

    status = "not configured"
    if configured:
        status = "configured"
    click.echo(f"runpod  {status}")
