from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from galloc.api_clients.exceptions import APIRequestFailed
from galloc.core import services, storage
from galloc.core.exceptions import GPUConflict, GPUNotFound, InvalidTTL
from galloc.core.validators import (
    prepare_creation,
    prepare_extension,
    prepare_instance_operation,
)
from galloc.providers.exceptions import InvalidGPU, InvalidRegion


def test_creation_preparation_resolves_inputs_without_saving(connection, provider, creation_arguments):
    """Return provider identifiers and a valid deadline before any mutation occurs."""

    provider.validate_create.return_value = ("gpu-id", "region-id", Decimal("3"))
    creation = prepare_creation(connection, provider, **creation_arguments)

    assert (creation.gpu_model, creation.region) == ("gpu-id", "region-id")
    assert creation.duration == timedelta(minutes=30)
    assert creation.hourly_price == Decimal("3")
    assert storage.get_gpu_list(connection) == []
    provider.validate_create.assert_called_once_with("H200", "test")
    provider.create.assert_not_called()


def test_invalid_ttl_fails_before_provider_access(connection, provider, creation_arguments):
    """Reject invalid TTL before catalogs, persistence, or resource creation."""

    creation_arguments["ttl"] = "0m"
    with pytest.raises(InvalidTTL):
        prepare_creation(connection, provider, **creation_arguments)

    assert storage.get_gpu_list(connection) == []
    assert provider.mock_calls == []


@pytest.mark.parametrize(
    "failure", [InvalidGPU("Unknown GPU."), InvalidRegion("Unknown region."), APIRequestFailed("Catalog failed.")]
)
def test_provider_validation_failure_leaves_no_creation_intent(connection, provider, creation_arguments, failure):
    """Reject bad provider inputs before reserving a local mapping."""

    provider.validate_create.side_effect = failure
    with pytest.raises(type(failure)):
        prepare_creation(connection, provider, **creation_arguments)

    assert storage.get_gpu_list(connection) == []
    provider.create.assert_not_called()


def test_duplicate_id_fails_before_provider_access(connection, provider, gpu, creation_arguments):
    """Reject an existing ID before contacting the provider or changing its mapping."""

    storage.create_gpu(connection, gpu)

    with pytest.raises(GPUConflict, match="already exists"):
        prepare_creation(connection, provider, gpu_id=gpu.id, **creation_arguments)

    assert storage.get_gpu(connection, gpu.id) == gpu
    assert provider.mock_calls == []


def test_extension_rejects_absent_instance(connection, gpu):
    """Require a provider-confirmed instance before changing its deadline."""

    storage.create_gpu(connection, gpu)
    with pytest.raises(GPUNotFound, match="was not found"):
        prepare_extension(connection, gpu.id, "1h", None)
    assert storage.get_gpu(connection, gpu.id) == gpu


def test_invalid_extension_leaves_deadline_unchanged(connection, gpu, instance):
    """Finish failed preparation without changing the record or holding a transaction."""

    storage.create_gpu(connection, gpu)
    with pytest.raises(InvalidTTL), storage.transaction(connection):
        prepare_extension(connection, gpu.id, "-1h", instance)

    assert not connection.in_transaction
    assert storage.get_gpu(connection, gpu.id) == gpu
    with storage.transaction(connection):
        extension = prepare_extension(connection, gpu.id, "30m", instance)
        assert storage.get_gpu(connection, gpu.id) == gpu
        extended_gpu = services.extend_gpu(connection, extension)
    assert extended_gpu.expires_at == gpu.expires_at + timedelta(minutes=30)


def test_missing_gpu_fails_during_preparation(connection):
    """Load the required entity before allowing an instance operation to start."""

    with pytest.raises(GPUNotFound):
        prepare_instance_operation(connection, uuid4())
