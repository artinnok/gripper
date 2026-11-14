from pathlib import Path

import click

from galloc.providers.factory import PROVIDER_NAME_LIST, open_provider
from galloc.settings import Settings
from galloc.ssh_keys import read_public_key
from galloc.terminal.ssh_keys import print_ssh_key_creation


@click.command("add")
@click.argument("public_key_path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path))
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), required=True)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def add_command(settings: Settings, public_key_path: Path, provider_name: str, output: str) -> None:
    """Register a public key with a provider."""

    ssh_key = read_public_key(public_key_path)
    with open_provider(settings, provider_name) as provider:
        created = provider.create_ssh_key(ssh_key)

    print_ssh_key_creation(ssh_key.fingerprint, created, output)
