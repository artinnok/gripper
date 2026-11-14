import click

from galloc.providers.factory import PROVIDER_NAME_LIST, open_provider
from galloc.settings import Settings
from galloc.terminal.parameters import parse_ssh_key_fingerprint
from galloc.terminal.ssh_keys import print_ssh_key_removal


@click.command("rm")
@click.argument("fingerprint", callback=parse_ssh_key_fingerprint)
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), required=True)
@click.option("--yes", "-y", is_flag=True, help="Skip removal confirmation.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def rm_command(settings: Settings, fingerprint: str, provider_name: str, yes: bool, output: str) -> None:
    """Remove a public key registration."""

    with open_provider(settings, provider_name) as provider:
        if not yes:
            click.confirm(f"Remove SSH key {fingerprint} from {provider_name}?", abort=True, err=True)
        removed = provider.delete_ssh_key(fingerprint)

    print_ssh_key_removal(fingerprint, removed, output)
