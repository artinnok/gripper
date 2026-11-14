from uuid import UUID

import click

from galloc.core.services import delete_gpu
from galloc.core.storage import open_database
from galloc.core.validators import prepare_instance_operation
from galloc.providers.factory import open_provider
from galloc.settings import Settings
from galloc.terminal.output import print_removal
from galloc.terminal.parameters import parse_gpu_reference


@click.command("rm")
@click.argument("gpu_id", metavar="NAME", callback=parse_gpu_reference)
@click.option("--yes", "-y", is_flag=True, help="Skip deletion confirmation.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def delete_command(settings: Settings, gpu_id: UUID | str, yes: bool, output: str) -> None:
    """Delete a GPU instance."""

    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            if not yes:
                click.confirm(f"Delete {gpu.name} and its resources?", abort=True, err=True)
            delete_gpu(connection, provider, gpu)

    print_removal(gpu, output)
