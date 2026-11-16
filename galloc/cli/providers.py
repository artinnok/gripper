import json

import click

from galloc.providers.factory import PROVIDER_NAME_LIST
from galloc.settings import Settings


@click.command("providers")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def providers_command(settings: Settings, output: str) -> None:
    """List providers and configuration status."""

    provider_list = []
    for name in PROVIDER_NAME_LIST:
        api_key = getattr(settings, f"{name}_api_key")
        configured = api_key is not None and bool(api_key.get_secret_value().strip())
        provider_list.append({"name": name, "configured": configured})

    if output == "json":
        click.echo(json.dumps(provider_list, indent=2))
        return

    for provider in provider_list:
        status = "configured" if provider["configured"] else "not configured"
        click.echo(f"{provider['name']}  {status}")
