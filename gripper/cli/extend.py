from uuid import UUID

import click

from gripper.core.services import extend_gpu, get_gpu
from gripper.core.ssh_keys import configured_key_path
from gripper.core.storage import lock_deadlines, open_database, transaction
from gripper.core.ttl import parse_ttl
from gripper.core.validators import prepare_extension, prepare_instance_operation
from gripper.providers.factory import open_provider
from gripper.settings import Settings
from gripper.terminal.output import print_gpu
from gripper.terminal.parameters import parse_gpu_reference


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
        with lock_deadlines(settings.database_path), transaction(connection):
            gpu = prepare_extension(connection, gpu_id, ttl, instance)
            gpu = extend_gpu(connection, gpu)

    print_gpu(gpu, output, instance, configured_key_path(settings, gpu.provider))
