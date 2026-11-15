from pathlib import Path
from uuid import UUID

from galloc.config import save_config
from galloc.core import services
from galloc.core.exceptions import GPUCreationPending
from galloc.core.schemas import GPU
from galloc.core.ssh_keys import generate_key_pair, read_public_key
from galloc.core.storage import get_linked_gpu_map, open_database, transaction
from galloc.core.ttl import parse_ttl
from galloc.core.validators import prepare_creation, prepare_extension, prepare_instance_operation
from galloc.exceptions import GallocError
from galloc.providers.exceptions import CreationRejected, GPUUnavailable
from galloc.providers.factory import get_ssh_public_key_path, open_provider
from galloc.providers.schemas import GPUModel, Instance, Region
from galloc.settings import Settings


def load_gpu_list(settings: Settings) -> list[tuple[GPU, Instance]]:
    """Read mapped GPUs with their current provider state."""

    result_list = []
    with open_database(settings.database_path) as connection:
        gpu_map = get_linked_gpu_map(connection)
        for provider_name, gpu_list in gpu_map.items():
            with open_provider(settings, provider_name) as provider:
                result_list.extend(services.get_gpu_list(connection, provider, gpu_list))

    return result_list


def load_gpu_model_list(settings: Settings) -> list[GPUModel]:
    """Read the GPU models the configured provider can create now."""

    with open_provider(settings, settings.provider) as provider:
        gpu_model_list = provider.get_gpu_list()

    if not gpu_model_list:
        raise GPUUnavailable(f"No GPUs currently available for {settings.provider}.")
    return gpu_model_list


def load_region_list(settings: Settings, gpu_model: str) -> list[Region]:
    """Read the regions that currently have capacity for the chosen GPU model."""

    with open_provider(settings, settings.provider) as provider:
        region_list = provider.get_region_list(gpu_model)

    if not region_list:
        raise GPUUnavailable(f"No regions currently available for {gpu_model} on {settings.provider}.")
    return region_list


def create(settings: Settings, gpu_model: str, region: str, ttl: str) -> str:
    """Create a billable GPU with the configured provider and name an uncertain result."""

    provider_name = settings.provider
    public_key_path = get_ssh_public_key_path(settings, provider_name)
    ssh_key = read_public_key(public_key_path) if public_key_path is not None else None
    with open_database(settings.database_path) as connection, open_provider(settings, provider_name) as provider:
        creation = prepare_creation(
            connection, provider, provider_name=provider_name, gpu=gpu_model, region=region, ttl=ttl
        )
        if ssh_key is not None:
            provider.create_ssh_key(ssh_key)
        try:
            gpu, instance = services.create_gpu(connection, provider, creation)
        except CreationRejected:
            raise
        except GallocError as exception:
            raise GPUCreationPending(
                f"{exception} Creation of {creation.gpu.name} is unresolved. "
                f"Check galloc-{creation.gpu.id} in the {provider_name} console."
            ) from exception

    return f"Created {gpu.name}"


def extend(settings: Settings, gpu_id: UUID, ttl: str) -> None:
    """Add time to the deadline of a GPU that still exists at its provider."""

    parse_ttl(ttl)
    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            gpu, instance = services.get_gpu(connection, provider, gpu)
        with transaction(connection):
            gpu = prepare_extension(connection, gpu_id, ttl, instance)
            services.extend_gpu(connection, gpu)


def delete(settings: Settings, gpu_id: UUID) -> str:
    """Delete a GPU through its provider, remove its local mapping, and name what was deleted."""

    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            services.delete_gpu(connection, provider, gpu)

    return f"Deleted {gpu.name}"


def add_ssh_key(settings: Settings, public_key_path: Path) -> str:
    """Register a public key file with the configured provider and describe the outcome."""

    ssh_key = read_public_key(public_key_path)
    with open_provider(settings, settings.provider) as provider:
        created = provider.create_ssh_key(ssh_key)

    status = "Registered" if created else "Already registered"
    return f"{status}  {ssh_key.fingerprint}"


def check_api_key(settings: Settings) -> None:
    """Confirm that the entered key can read the selected provider's catalog."""

    with open_provider(settings, settings.provider) as provider:
        provider.get_region_list()


def finish_setup(settings: Settings, config_path: Path, api_key: str, public_key_path: Path | None) -> Path:
    """Register the chosen or newly generated public key, then save the configuration."""

    if public_key_path is None:
        public_key_path = generate_key_pair(Path.home() / ".ssh" / "galloc_ed25519")
    add_ssh_key(settings, public_key_path)
    save_config(config_path, provider_name=settings.provider, api_key=api_key, ssh_public_key_path=public_key_path)
    return public_key_path
