import json
from datetime import timedelta
from uuid import uuid4

import httpx

from galloc.core import services, storage
from galloc.core.validators import (
    prepare_creation,
    prepare_extension,
    prepare_instance_operation,
)
from galloc.providers.runpod.provider import RunpodProvider
from galloc.providers.schemas import InstanceStatus
from tests.conftest import make_gpu_data


def test_runpod_lifecycle_through_all_layers(tmp_path, make_runpod_client, pod_data):
    """Exercise the real application layers, replacing only Runpod's HTTP responses."""

    gpu_id = uuid4()
    pod_data["name"] = f"galloc-{gpu_id}"
    pod_path = f"/v2/pods/{pod_data['id']}"
    exchange_list = [
        (
            "GET",
            "/v2/catalog/gpus",
            httpx.Response(
                200, json={"gpus": [pod_data["gpu"] | make_gpu_data(name="H200", secure=True, price={"secure": 5.29})]}
            ),
        ),
        (
            "GET",
            "/v2/catalog/datacenters",
            httpx.Response(
                200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
            ),
        ),
        ("POST", "/v2/pods", httpx.Response(201, json=pod_data | {"status": "PROVISIONING"})),
        ("GET", pod_path, httpx.Response(200, json=pod_data)),
        ("GET", pod_path, httpx.Response(200, json=pod_data)),
        ("DELETE", pod_path, httpx.Response(204)),
        ("DELETE", pod_path, httpx.Response(404)),
        ("GET", pod_path, httpx.Response(404)),
    ]
    exchange_iterator = iter(exchange_list)
    request_list = []

    def handler(request):
        """Verify each outgoing request and return the corresponding provider response."""

        method, path, response = next(exchange_iterator)
        assert (request.method, request.url.path) == (method, path)
        request_list.append(request)
        return response

    provider = RunpodProvider(make_runpod_client(handler), image="example/image:1", disk_gb=50)
    database_path = tmp_path / "state.db"
    with storage.open_database(database_path) as connection:
        creation = prepare_creation(
            connection,
            provider,
            provider_name="runpod",
            gpu="h200",
            region=" us-ks-2 ",
            ttl="30m",
            gpu_id=gpu_id,
        )
        assert storage.get_gpu_list(connection) == []
        assert len(request_list) == 2
        gpu, instance = services.create_gpu(connection, provider, creation)

    assert instance.status == InstanceStatus.PROVISIONING
    assert instance.gpu == "NVIDIA H200"
    _, _, creation_request = request_list
    payload = json.loads(creation_request.content)
    assert payload["name"] == f"galloc-{gpu_id}"
    assert payload["gpu"] == {"id": "NVIDIA H200", "count": 1}

    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu_id) == gpu
        instance = provider.get(gpu.provider_instance_id)
        assert instance.status == InstanceStatus.RUNNING

        prepared_gpu = prepare_instance_operation(connection, gpu_id)
        saved_gpu, instance = services.get_gpu(connection, provider, prepared_gpu)
        assert saved_gpu == gpu
        assert instance.status == InstanceStatus.RUNNING
        assert instance.ssh_command == "ssh root@195.26.233.3 -p 34446"

        with storage.transaction(connection):
            extension = prepare_extension(connection, gpu_id, "1h", instance)
            services.extend_gpu(connection, extension)
        assert extension.expires_at == gpu.expires_at + timedelta(hours=1)

        deletion = prepare_instance_operation(connection, gpu_id)
        for _ in range(2):
            services.delete_gpu(connection, provider, deletion)
        assert services.get_gpu(connection, provider, deletion) == (deletion, None)
        assert services.get_gpu_list(connection, provider, []) == []

    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
    assert len(request_list) == len(exchange_list)
