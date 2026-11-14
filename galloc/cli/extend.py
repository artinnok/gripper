from uuid import UUID

import click

from galloc.core.services import extend_gpu, get_gpu
from galloc.core.storage import open_database, transaction
from galloc.core.ttl import parse_ttl
from galloc.core.validators import prepare_extension, prepare_instance_operation
from galloc.providers.factory import open_provider
from galloc.settings import Settings
from galloc.terminal.output import print_gpu
from galloc.terminal.parameters import parse_gpu_reference


@click.command("extend")
@click.argument("gpu_id", metavar="NAME", callback=parse_gpu_reference)
@click.option("--ttl", required=True, help="Time to add to the current deadline, such as 30m or 1d.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def extend_command(settings: Settings, gpu_id: UUID | str, ttl: str, output: str) -> None:
    """Extend a GPU instance TTL."""

    parse_ttl(ttl)
    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            gpu, instance = get_gpu(connection, provider, gpu)
        with transaction(connection):
            gpu = prepare_extension(connection, gpu_id, ttl, instance)
            gpu = extend_gpu(connection, gpu)

    print_gpu(gpu, output, instance)
