from pathlib import Path

import click

from gripper.core.ssh_keys import read_public_key
from gripper.providers.factory import PROVIDER_NAME_LIST, open_provider
from gripper.settings import Settings
from gripper.terminal.ssh_keys import print_ssh_key_creation


@click.command("add")
@click.argument("public_key_path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path))
@click.option(
    "--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), help="Override the configured provider."
)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def add_command(settings: Settings, public_key_path: Path, provider_name: str | None, output: str) -> None:
    """Register a public key with a provider."""

    provider_name = provider_name or settings.provider
    ssh_key = read_public_key(public_key_path.expanduser())
    with open_provider(settings, provider_name) as provider:
        created = provider.create_ssh_key(ssh_key)

    print_ssh_key_creation(ssh_key.fingerprint, created, output)
