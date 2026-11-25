import json

import click

from gripper.providers.factory import PROVIDER_NAME_LIST, open_provider
from gripper.settings import Settings


ID_COLUMN_WIDTH = 14


@click.command("regions")
@click.option(
    "--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), help="Override the configured provider."
)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def regions_command(settings: Settings, provider_name: str | None, output: str) -> None:
    """List provider regions."""

    with open_provider(settings, provider_name or settings.provider) as provider:
        region_list = provider.get_region_list()

    if output == "json":
        click.echo(json.dumps([region.model_dump(mode="json") for region in region_list], indent=2))
        return
    if not region_list:
        click.echo("No regions available.")
        return

    click.echo(f"{'ID':<{ID_COLUMN_WIDTH}}  LOCATION")
    for region in region_list:
        click.echo(f"{region.id:<{ID_COLUMN_WIDTH}}  {region.location}")
