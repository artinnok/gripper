import click

from galloc.providers.factory import PROVIDER_NAME_LIST, open_provider
from galloc.settings import Settings
from galloc.terminal.ssh_keys import print_ssh_key_list


@click.command("ls")
@click.option(
    "--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), help="Override the configured provider."
)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def ls_command(settings: Settings, provider_name: str | None, output: str) -> None:
    """List registered public keys."""

    with open_provider(settings, provider_name or settings.provider) as provider:
        ssh_key_list = provider.get_ssh_key_list()

    print_ssh_key_list(ssh_key_list, output)
