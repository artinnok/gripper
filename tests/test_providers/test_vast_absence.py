from datetime import UTC, datetime, timedelta

import httpx
import pytest

from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse
from gripper.core import services, storage
from gripper.core.schemas import GPU
from gripper.providers.vast.provider import VastProvider


@pytest.mark.parametrize(
    "method, status, payload, error",
    [
        ("GET", 200, {"instances": None}, None),
        ("GET", 404, {"success": False, "error": "not_found"}, None),
        ("GET", 404, {"success": False, "error": "auth_error"}, APIRequestFailed),
        ("GET", 404, {}, APIRequestFailed),
        ("GET", 403, {}, APIRequestFailed),
        ("GET", 500, {}, APIRequestFailed),
        ("GET", 200, {}, InvalidAPIResponse),
        ("DELETE", 200, {"success": True}, None),
        ("DELETE", 404, {"success": False, "error": "not_found"}, None),
        ("DELETE", 404, {"success": False, "error": "auth_error"}, APIRequestFailed),
        ("DELETE", 404, {}, APIRequestFailed),
        ("DELETE", 200, {"success": False}, APIRequestFailed),
        ("DELETE", 200, {}, InvalidAPIResponse),
        ("DELETE", 403, {}, APIRequestFailed),
        ("DELETE", 500, {}, APIRequestFailed),
    ],
)
def test_mapping_removed_only_after_confirmation(tmp_path, make_vast_client, method, status, payload, error):
    """Never lose a deadline because an instance read or deletion failed."""

    def handler(request):
        """Return the specified provider result for the mapped contract."""

        assert request.method == method
        assert request.url.path == "/api/v0/instances/5678/"
        return httpx.Response(status, json=payload)

    gpu = GPU(provider="vast", provider_instance_id="5678", expires_at=datetime.now(UTC) + timedelta(hours=1))
    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    operation = services.get_gpu if method == "GET" else services.delete_gpu
    with storage.open_database(tmp_path / "state.db") as connection:
        storage.create_gpu(connection, gpu)
        if error is not None:
            with pytest.raises(error):
                operation(connection, provider, gpu)
            assert storage.get_gpu_list(connection) == [gpu]
        else:
            operation(connection, provider, gpu)
            assert storage.get_gpu_list(connection) == []
