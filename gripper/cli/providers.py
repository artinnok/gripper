import json

import click

from gripper.providers.factory import PROVIDER_NAME_LIST, is_configured
from gripper.settings import Settings


@click.command("providers")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def providers_command(settings: Settings, output: str) -> None:
    """List providers and configuration status."""

    provider_list = [{"name": name, "configured": is_configured(settings, name)} for name in PROVIDER_NAME_LIST]

    if output == "json":
        click.echo(json.dumps(provider_list, indent=2))
        return

    for provider in provider_list:
        status = "configured" if provider["configured"] else "not configured"
        click.echo(f"{provider['name']}  {status}")
