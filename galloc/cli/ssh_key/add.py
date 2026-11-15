from pathlib import Path

import click

from galloc.core.ssh_keys import read_public_key
from galloc.providers.factory import PROVIDER_NAME_LIST, get_ssh_public_key_path, open_provider
from galloc.settings import Settings
from galloc.terminal.ssh_keys import print_ssh_key_creation


@click.command("add")
@click.argument(
    "public_key_path", required=False, type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path)
)
@click.option(
    "--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), help="Override the configured provider."
)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def add_command(settings: Settings, public_key_path: Path | None, provider_name: str | None, output: str) -> None:
    """Register a public key with a provider."""

    provider_name = provider_name or settings.provider
    public_key_path = public_key_path or get_ssh_public_key_path(settings, provider_name)
    if public_key_path is None:
        raise click.UsageError("Pass a public key path or configure ssh_public_key.")
    ssh_key = read_public_key(public_key_path.expanduser())
    with open_provider(settings, provider_name) as provider:
        created = provider.create_ssh_key(ssh_key)

    print_ssh_key_creation(ssh_key.fingerprint, created, output)
