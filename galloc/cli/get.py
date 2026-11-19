from uuid import UUID

import click

from galloc.core import services, storage
from galloc.core.exceptions import GPUNotFound
from galloc.core.ssh_keys import configured_key_path
from galloc.core.validators import prepare_instance_operation
from galloc.providers.factory import open_provider
from galloc.settings import Settings
from galloc.terminal.output import print_gpu
from galloc.terminal.parameters import parse_gpu_reference


@click.command("get")
@click.argument("gpu_id", metavar="NAME", callback=parse_gpu_reference)
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def get_command(settings: Settings, gpu_id: UUID | str, output: str) -> None:
    """Show GPU status and connection details."""

    with storage.open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            gpu, instance = services.get_gpu(connection, provider, gpu)

    if instance is None:
        raise GPUNotFound(f"GPU {gpu.name} was not found.")
    print_gpu(gpu, output, instance, configured_key_path(settings, gpu.provider))
