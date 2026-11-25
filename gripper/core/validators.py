import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from gripper.core import storage
from gripper.core.exceptions import GPUConflict, GPUCreationPending, GPUNotFound, InvalidBudget
from gripper.core.pricing import calculate_budget_ttl, parse_budget
from gripper.core.schemas import GPU, Creation
from gripper.core.ttl import calculate_expires_at
from gripper.providers.base import Provider
from gripper.providers.schemas import Instance, InstanceStatus


def prepare_creation(
    connection: sqlite3.Connection,
    provider: Provider,
    *,
    provider_name: str,
    gpu: str,
    region: str,
    ttl: str | None = None,
    budget: str | None = None,
    gpu_id: UUID | None = None,
) -> Creation:
    """Validate creation inputs, keeping GPU details and pricing only in memory."""

    if ttl is None and budget is None:
        raise InvalidBudget("Supply exactly one of --ttl or --budget.")
    if ttl is not None and budget is not None:
        raise InvalidBudget("Supply exactly one of --ttl or --budget.")

    starts_at = datetime.now(UTC)
    if ttl is not None:
        expires_at = calculate_expires_at(ttl, starts_at)
    else:
        budget_amount = parse_budget(budget)

    gpu_id = gpu_id or uuid4()
    _validate_new_id(connection, gpu_id)

    gpu, region, hourly_price = provider.validate_create(gpu, region)
    if budget is not None:
        budget_ttl = calculate_budget_ttl(budget_amount, hourly_price)
        expires_at = calculate_expires_at(budget_ttl, starts_at)

    return Creation(
        gpu=GPU(id=gpu_id, provider=provider_name, expires_at=expires_at),
        gpu_model=gpu,
        region=region,
        duration=expires_at - starts_at,
        hourly_price=hourly_price,
    )


def prepare_extension(connection: sqlite3.Connection, gpu_id: UUID | str, ttl: str, instance: Instance | None) -> GPU:
    """Require a live instance and extend the latest local deadline before saving."""

    if instance is None or instance.status == InstanceStatus.DELETED:
        raise GPUNotFound(f"GPU {gpu_id} was not found.")
    gpu = storage.get_gpu(connection, gpu_id)
    expires_at = calculate_expires_at(ttl, gpu.expires_at)
    return gpu.model_copy(update={"expires_at": expires_at})


def prepare_instance_operation(connection: sqlite3.Connection, gpu_id: UUID | str) -> GPU:
    """Require a known provider ID before reading, extending, or deleting an instance."""

    gpu = storage.get_gpu(connection, gpu_id)
    if gpu.provider_instance_id is None:
        raise GPUCreationPending(
            f"Creation of {gpu.name} is unresolved. Search for {gpu.id} in the {gpu.provider} console."
        )
    return gpu


def prepare_expiration(connection: sqlite3.Connection, gpu_id: UUID, now: datetime) -> GPU | None:
    """Reload a candidate under the deadline lock and skip removed or extended GPUs."""

    try:
        gpu = storage.get_gpu(connection, gpu_id)
    except GPUNotFound:
        return None
    if gpu.expires_at > now:
        return None
    return gpu


def _validate_new_id(connection: sqlite3.Connection, gpu_id: UUID) -> None:
    """Reject duplicate mappings before a creation service can submit another request."""

    try:
        storage.get_gpu(connection, gpu_id)
    except GPUNotFound:
        return
    raise GPUConflict(f"GPU {gpu_id} already exists; use gripper get {gpu_id}.")
