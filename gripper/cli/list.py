import click

from gripper.core.services import get_gpu_list
from gripper.core.storage import get_linked_gpu_map, open_database
from gripper.providers.factory import open_provider
from gripper.settings import Settings
from gripper.terminal.output import print_gpu_list


@click.command("ls")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def list_command(settings: Settings, output: str) -> None:
    """List GPU instances."""

    result_list = []
    with open_database(settings.database_path) as connection:
        gpu_map = get_linked_gpu_map(connection)
        for provider_name, gpu_list in gpu_map.items():
            with open_provider(settings, provider_name) as provider:
                result_list.extend(get_gpu_list(connection, provider, gpu_list))

    print_gpu_list(result_list, output)
