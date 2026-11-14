import sqlite3

from galloc.core import storage
from galloc.core.schemas import GPU, Creation
from galloc.providers.base import Provider
from galloc.providers.exceptions import CreationRejected
from galloc.providers.runpod.schemas import Instance


def create_gpu(connection: sqlite3.Connection, provider: Provider, creation: Creation) -> tuple[GPU, Instance]:
    """Reserve a name, create once, and save only the returned instance link."""

    gpu = creation.gpu
    storage.create_gpu(connection, gpu)

    try:
        instance = provider.create(gpu.id, gpu=creation.gpu_model, region=creation.region)
    except CreationRejected:
        storage.delete_gpu(connection, gpu.id)
        raise

    gpu = _save_instance_id(connection, gpu, instance)
    return gpu, instance


def get_gpu_list(connection: sqlite3.Connection, provider: Provider, gpu_list: list[GPU]) -> list[tuple[GPU, Instance]]:
    """Read linked instances from one provider, keeping only those that still exist."""

    result_list = []
    for gpu in gpu_list:
        gpu, instance = get_gpu(connection, provider, gpu)
        if instance is not None:
            result_list.append((gpu, instance))

    return result_list


def get_gpu(connection: sqlite3.Connection, provider: Provider, gpu: GPU) -> tuple[GPU, Instance | None]:
    """Read current state and discard the mapping when the provider confirms absence."""

    instance = provider.get(gpu.provider_instance_id)
    if instance is None:
        storage.delete_gpu(connection, gpu.id)
    return gpu, instance


def extend_gpu(connection: sqlite3.Connection, gpu: GPU) -> GPU:
    """Save the deadline prepared and validated by the caller."""

    storage.update_gpu_expiration(connection, gpu)
    return gpu


def delete_gpu(connection: sqlite3.Connection, provider: Provider, gpu: GPU) -> None:
    """Delete through the provider, then remove the confirmed obsolete mapping."""

    provider.delete(gpu.provider_instance_id)
    storage.delete_gpu(connection, gpu.id)


def _save_instance_id(connection: sqlite3.Connection, gpu: GPU, instance: Instance) -> GPU:
    """Save only the provider ID, leaving all instance details at the provider."""

    gpu = gpu.model_copy(update={"provider_instance_id": instance.id})
    storage.update_gpu_instance(connection, gpu)
    return storage.get_gpu(connection, gpu.id)
