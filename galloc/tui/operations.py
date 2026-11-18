from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from galloc.config import save_config, save_config_value
from galloc.core import services
from galloc.core.exceptions import GPUCreationPending
from galloc.core.readiness import wait_for_instance
from galloc.core.schemas import GPU
from galloc.core.ssh_keys import SSHKey, read_public_key
from galloc.core.storage import get_linked_gpu_map, lock_deadlines, open_database, transaction
from galloc.core.ttl import parse_ttl
from galloc.core.validators import prepare_creation, prepare_extension, prepare_instance_operation
from galloc.exceptions import GallocError
from galloc.providers.exceptions import CreationRejected, GPUUnavailable
from galloc.providers.factory import PROVIDER_NAME_LIST, get_ssh_public_key_path, is_configured, open_provider
from galloc.providers.schemas import GPUModel, Instance, InstanceStatus, Region
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


def create(
    settings: Settings, gpu_model: str, region: str, ttl: str | None = None, budget: str | None = None
) -> tuple[GPU, Instance]:
    """Create a billable GPU with the configured provider and name an uncertain result."""

    provider_name = settings.provider
    public_key_path = get_ssh_public_key_path(settings, provider_name)
    ssh_key = read_public_key(public_key_path) if public_key_path is not None else None
    with open_database(settings.database_path) as connection, open_provider(settings, provider_name) as provider:
        creation = prepare_creation(
            connection, provider, provider_name=provider_name, gpu=gpu_model, region=region, ttl=ttl, budget=budget
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

    return gpu, instance


async def wait_for_gpu(settings: Settings, gpu: GPU, instance: Instance) -> Instance:
    """Await the same provider readiness as the CLI without blocking terminal input."""

    if instance.status == InstanceStatus.RUNNING:
        return instance
    with open_provider(settings, gpu.provider) as provider:
        return await wait_for_instance(
            provider, instance.id, timeout=settings.startup_timeout, poll_interval=settings.startup_poll_interval
        )


def extend(settings: Settings, gpu_id: UUID, ttl: str) -> None:
    """Add time to the deadline of a GPU that still exists at its provider."""

    parse_ttl(ttl)
    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            gpu, instance = services.get_gpu(connection, provider, gpu)
        with lock_deadlines(settings.database_path), transaction(connection):
            gpu = prepare_extension(connection, gpu_id, ttl, instance)
            services.extend_gpu(connection, gpu)


def delete(settings: Settings, gpu_id: UUID) -> str:
    """Delete a GPU through its provider, remove its local mapping, and name what was deleted."""

    with open_database(settings.database_path) as connection:
        gpu = prepare_instance_operation(connection, gpu_id)
        with open_provider(settings, gpu.provider) as provider:
            services.delete_gpu(connection, provider, gpu)

    return f"Deleted {gpu.name}"


def load_ssh_key_list(settings: Settings) -> tuple[list[tuple[str, SSHKey]], list[str]]:
    """Read the public keys registered with every provider that has an API key, beside any failures."""

    result_list, error_list = [], []
    for provider_name in [name for name in PROVIDER_NAME_LIST if is_configured(settings, name)]:
        try:
            with open_provider(settings, provider_name) as provider:
                result_list += [(provider_name, ssh_key) for ssh_key in provider.get_ssh_key_list()]
        except (GallocError, OSError) as exception:
            error_list.append(f"{provider_name}: {exception}")

    return result_list, error_list


def add_ssh_key(settings: Settings, public_key_path: Path) -> str:
    """Register a public key file with the configured provider and describe the outcome."""

    ssh_key = read_public_key(public_key_path)
    with open_provider(settings, settings.provider) as provider:
        created = provider.create_ssh_key(ssh_key)

    status = "Registered" if created else "Already registered"
    return f"{status}  {ssh_key.fingerprint}"


def remove_ssh_key(settings: Settings, provider_name: str, fingerprint: str) -> str:
    """Remove one provider's registration while keeping the user's local key files."""

    with open_provider(settings, provider_name) as provider:
        removed = provider.delete_ssh_key(fingerprint)
    return "SSH key removed" if removed else "SSH key already absent"


def check_api_key(settings: Settings) -> None:
    """Confirm access through the selected provider's authenticated read endpoint."""

    with open_provider(settings, settings.provider) as provider:
        provider.check_credentials()


def finish_setup(settings: Settings, config_path: Path, api_key: str, public_key_path: Path) -> None:
    """Register the selected public key, then save the complete configuration."""

    add_ssh_key(settings, public_key_path)
    save_config(
        config_path,
        provider_name=settings.provider,
        api_key=api_key,
        disk_gb=settings.disk_gb,
        ssh_public_key_path=public_key_path,
    )


def save_api_key(settings: Settings, config_path: Path, provider_name: str, api_key: str) -> Settings:
    """Verify one provider's API key, save it to the configuration, and return settings that use it."""

    settings = settings.model_copy(update={f"{provider_name}_api_key": SecretStr(api_key)})
    with open_provider(settings, provider_name) as provider:
        provider.check_credentials()

    save_config_value(config_path, f"providers.{provider_name}", "api_key", api_key)
    return settings


def use_provider(settings: Settings, config_path: Path, provider_name: str) -> Settings:
    """Save the default provider of the SSH key screen and CLI commands, and return settings that use it."""

    save_config_value(config_path, "defaults", "provider", provider_name)
    return settings.model_copy(update={"provider": provider_name})
