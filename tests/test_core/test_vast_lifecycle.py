import json
from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from pydantic import SecretStr

from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse
from gripper.core import services, storage
from gripper.core.validators import prepare_creation, prepare_extension
from gripper.providers.exceptions import CreationRejected
from gripper.providers.factory import open_provider
from gripper.providers.vast.provider import VastProvider
from gripper.settings import Settings
from gripper.tui.expiration import delete_expired_gpu_list


def test_creation_saves_id_before_polling_and_expiration_retries(
    tmp_path, vast_http, vast_offer_data, vast_instance_data
):
    """Use real Core and TTL operations through the selected Vast.ai adapter without Runpod credentials."""

    settings = Settings(provider="vast", vast_api_key=SecretStr("vast-token"), database_path=tmp_path / "state.db")
    delete_status = 500

    def handler(request):
        """Return a contract once, read it on demand, and temporarily refuse its deletion."""

        if request.url.path.endswith("bundles/"):
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        if request.method == "PUT":
            payload = json.loads(request.content)
            assert payload["cancel_unavail"] is True
            assert payload["disk"] == 50 and payload["runtype"] == "ssh"
            return httpx.Response(200, json={"success": True, "new_contract": 5678})
        if request.method == "DELETE":
            return httpx.Response(delete_status, json={"success": delete_status == 200})
        return httpx.Response(200, json={"instances": vast_instance_data})

    vast_http.side_effect = handler
    with storage.open_database(settings.database_path) as connection, open_provider(settings, "vast") as provider:
        creation = prepare_creation(connection, provider, provider_name="vast", gpu="1234", region="US", budget="2.5")
        assert creation.duration == timedelta(minutes=500)
        gpu, instance = services.create_gpu(connection, provider, creation)
        assert gpu.provider_instance_id == "5678"
        assert all(call.args[0].method != "GET" for call in vast_http.call_args_list)
        saved_gpu, instance = services.get_gpu(connection, provider, gpu)
        assert instance.ssh_command == "ssh root@ssh1.vast.ai -p 12345"
        extended = prepare_extension(connection, gpu.id, "1h", instance)
        services.extend_gpu(connection, extended)
        assert extended.expires_at == gpu.expires_at + timedelta(hours=1)
        extended.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        storage.update_gpu_expiration(connection, extended)

    deleted, errors = delete_expired_gpu_list(settings, Event())
    assert not deleted and len(errors) == 1
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, gpu.id).provider_instance_id == "5678"
    delete_status = 200
    assert delete_expired_gpu_list(settings, Event()) == ([gpu.name], [])
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize(
    "status, payload, expected, retained",
    [
        (410, {"success": False}, CreationRejected, False),
        (403, {"success": False}, CreationRejected, False),
        (200, {"success": False}, CreationRejected, False),
        (500, {}, APIRequestFailed, True),
        (200, {"success": True}, InvalidAPIResponse, True),
    ],
)
def test_refused_and_uncertain_creations_are_distinct(
    tmp_path, make_vast_client, vast_offer_data, status, payload, expected, retained
):
    """Remove a reservation only after a definite refusal, without retrying a billable request."""

    request_list = []

    def handler(request):
        """Make stock validation succeed before returning the specified creation result."""

        request_list.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        return httpx.Response(status, json=payload)

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    with storage.open_database(tmp_path / "state.db") as connection:
        creation = prepare_creation(connection, provider, provider_name="vast", gpu="1234", region="US", ttl="10m")
        with pytest.raises(expected):
            services.create_gpu(connection, provider, creation)
        assert bool(storage.get_gpu_list(connection)) is retained
    assert len(request_list) == 2
