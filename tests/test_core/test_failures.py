from unittest.mock import call

import pytest

from galloc.api_clients.exceptions import APIRequestFailed
from galloc.core import services, storage
from galloc.core.exceptions import GPUCreationPending
from galloc.core.validators import prepare_creation, prepare_instance_operation
from galloc.providers.exceptions import CreationRejected


@pytest.mark.parametrize("failure", [APIRequestFailed("Create timed out."), KeyboardInterrupt()])
def test_lost_response_preserves_mapping_without_retry(tmp_path, provider, creation_arguments, failure):
    """Keep the original identity after a lost response without reissuing creation."""

    provider.create.side_effect = failure
    with storage.open_database(tmp_path / "state.db") as connection:
        creation = prepare_creation(connection, provider, **creation_arguments)
        with pytest.raises(type(failure)):
            services.create_gpu(connection, provider, creation)
        gpu = storage.get_gpu(connection, creation.gpu.id)
        assert gpu.provider_instance_id is None
    with storage.open_database(tmp_path / "state.db") as connection:
        assert storage.get_gpu(connection, gpu.name) == gpu
    assert provider.create.call_count == 1


def test_confirmed_refusal_discards_empty_mapping(connection, provider, prepared_creation):
    """Remove a reserved name only when the provider confirms no resource was created."""

    provider.create.side_effect = CreationRejected("HTTP 402.")
    with pytest.raises(CreationRejected):
        services.create_gpu(connection, provider, prepared_creation)
    assert storage.get_gpu_list(connection) == []
    provider.delete.assert_not_called()


def test_failed_delete_preserves_mapping_for_retry(tmp_path, provider, gpu):
    """Keep the instance identity and TTL after deletion fails, without a local operation log."""

    provider.delete.side_effect = APIRequestFailed("Delete timed out.")
    with storage.open_database(tmp_path / "state.db") as connection:
        storage.create_gpu(connection, gpu)
        with pytest.raises(APIRequestFailed):
            services.delete_gpu(connection, provider, gpu)
        assert storage.get_gpu(connection, gpu.name) == gpu
    provider.delete.side_effect = None
    with storage.open_database(tmp_path / "state.db") as connection:
        deletion = prepare_instance_operation(connection, gpu.name)
        services.delete_gpu(connection, provider, deletion)
    assert provider.delete.call_args_list == [call("instance-1"), call("instance-1")]


def test_operation_requires_provider_id(connection, gpu):
    """Require a provider ID before attempting deletion or extension."""

    gpu.provider_instance_id = None
    storage.create_gpu(connection, gpu)
    with pytest.raises(GPUCreationPending):
        prepare_instance_operation(connection, gpu.name)
