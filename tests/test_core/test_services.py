import sqlite3
from datetime import timedelta
from unittest.mock import call

import pytest

from galloc.core import services, storage
from galloc.core.validators import prepare_creation, prepare_extension
from galloc.providers.schemas import InstanceStatus


def test_full_lifecycle(connection, provider, instance, prepared_creation, tmp_path):
    """Create and manage an instance without persisting its remote state."""

    gpu, created = services.create_gpu(connection, provider, prepared_creation)
    assert created == instance
    assert gpu.provider_instance_id == instance.id
    assert gpu.expires_at == prepared_creation.gpu.expires_at
    provider.create.assert_called_once_with(gpu.id, gpu="H200", region="test")
    assert services.get_gpu(connection, provider, gpu) == (gpu, instance)
    with storage.transaction(connection):
        extension = prepare_extension(connection, gpu.name, "1d", instance)
        extended = services.extend_gpu(connection, extension)
    assert extended.expires_at == gpu.expires_at + timedelta(days=1)
    services.delete_gpu(connection, provider, extended)
    provider.delete.assert_called_once_with(instance.id)
    with storage.open_database(tmp_path / "state.db") as reopened:
        assert storage.get_gpu_list(reopened) == []


def test_two_prepared_requests_cannot_create_twice(connection, provider, prepared_creation, creation_arguments):
    """Protect creation from two callers that prepared the same ID before insertion."""

    other = prepare_creation(connection, provider, gpu_id=prepared_creation.gpu.id, **creation_arguments)
    gpu, instance = services.create_gpu(connection, provider, prepared_creation)
    with pytest.raises(sqlite3.IntegrityError):
        services.create_gpu(connection, provider, other)
    assert storage.get_gpu(connection, gpu.id) == gpu
    assert provider.create.call_count == 1


def test_mapping_is_committed_before_api(connection, provider, instance, prepared_creation, tmp_path):
    """Preserve a concurrent deadline update when the provider finishes creation."""

    def create(gpu_id, gpu, region):
        """Observe the mapping from a separate connection during the external request."""

        assert not connection.in_transaction
        with storage.open_database(tmp_path / "state.db") as other:
            stored_gpu = storage.get_gpu(other, gpu_id)
            assert stored_gpu.provider_instance_id is None
            stored_gpu.expires_at += timedelta(hours=2)
            storage.update_gpu_expiration(other, stored_gpu)
        return instance

    provider.create.side_effect = create
    gpu, created = services.create_gpu(connection, provider, prepared_creation)
    assert gpu.expires_at == prepared_creation.gpu.expires_at + timedelta(hours=2)


@pytest.mark.parametrize("status", list(InstanceStatus))
def test_reading_does_not_cache_provider_state(connection, provider, gpu, instance, status):
    """Read current state while leaving the complete local row unchanged."""

    storage.create_gpu(connection, gpu)
    before = dict(connection.execute("SELECT * FROM gpus").fetchone())
    instance.status = status
    assert services.get_gpu(connection, provider, gpu) == (gpu, instance)
    assert dict(connection.execute("SELECT * FROM gpus").fetchone()) == before


def test_repeated_deletion_uses_provider_confirmation(connection, provider, gpu):
    """Accept repeated provider confirmation while keeping no deleted mapping."""

    storage.create_gpu(connection, gpu)
    for _ in range(2):
        services.delete_gpu(connection, provider, gpu)
    assert provider.delete.call_args_list == [call("instance-1"), call("instance-1")]
    assert storage.get_gpu_list(connection) == []
