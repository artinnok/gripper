import json
from datetime import timedelta

import httpx

from gripper.core import storage
from gripper.core.schemas import GPU
from gripper.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME


def test_lifecycle_through_cli(runner, runpod_http, database_path, pod_data):
    """Wait for creation, then read, extend, and delete using the short name."""

    pod_path = f"/v2/pods/{pod_data['id']}"
    exchange_list = [
        (
            "GET",
            "/v2/catalog/gpus",
            httpx.Response(
                200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
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
        ("GET", pod_path, httpx.Response(200, json=pod_data)),
        ("GET", pod_path, httpx.Response(200, json=pod_data)),
        ("DELETE", pod_path, httpx.Response(204)),
    ]
    exchange_iterator = iter(exchange_list)

    def handler(request):
        """Check the exact sequence of remote operations across separate commands."""

        method, path, response = next(exchange_iterator)
        assert (request.method, request.url.path) == (method, path)
        return response

    runpod_http.side_effect = handler
    result = runner.invoke(cli, ["add", "--gpu", "h200", "--region", "US-KS-2", "--ttl", "30m", "--output", "json"])
    assert result.exit_code == 0, result.output
    gpu = GPU.model_validate_json(result.stdout)
    gpu_id = str(gpu.id)
    gpu_name = gpu.name
    assert gpu_name == "gpu-1"
    assert json.loads(result.stdout)["status"] == "running"
    assert json.loads(result.stdout)["instance"]["gpu"] == "NVIDIA H200"
    assert gpu.expires_at - CREATION_TIME == timedelta(minutes=30)
    assert f"gripper rm {gpu_name}" in result.stderr

    first_call, second_call, creation_call, readiness_call = runpod_http.call_args_list
    (creation_request,) = creation_call.args
    assert json.loads(creation_request.content)["name"] == f"gripper-{gpu_id}"
    result = runner.invoke(cli, ["ls", "--output", "json"])
    assert result.exit_code == 0
    (listed,) = json.loads(result.stdout)
    assert listed["status"] == "running"
    assert listed["name"] == gpu_name

    result = runner.invoke(cli, ["get", gpu_name, "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "running"
    assert data["instance"]["status"] == "running"
    assert data["instance"]["ssh_command"] == "ssh root@195.26.233.3 -p 34446"
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id) == gpu

    result = runner.invoke(cli, ["extend", gpu_name, "--ttl", "1h", "--output", "json"])
    assert result.exit_code == 0
    extended = GPU.model_validate_json(result.stdout)
    assert extended.expires_at == gpu.expires_at + timedelta(hours=1)

    result = runner.invoke(cli, ["rm", gpu_name, "--yes", "--output", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"name": gpu_name, "removed": True}
    result = runner.invoke(cli, ["get", gpu_name, "--output", "json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "was not found" in result.stderr
    result = runner.invoke(cli, ["ls", "--output", "json"])
    assert json.loads(result.stdout) == []
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
    assert runpod_http.call_count == len(exchange_list)


def test_delete_confirmation(runner, runpod_http, gpu, database_path):
    """Do not change local or remote state until the user confirms deletion."""

    result = runner.invoke(cli, ["rm", str(gpu.id)], input="n\n")

    assert result.exit_code == 1
    assert "Aborted!" in result.stderr
    runpod_http.assert_not_called()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id) == gpu

    runpod_http.side_effect = [httpx.Response(204)]
    result = runner.invoke(cli, ["rm", str(gpu.id)], input="y\n")

    assert result.exit_code == 0
    assert result.stdout == f"{gpu.name}\n"
    (request,) = runpod_http.call_args.args
    assert request.method == "DELETE"


def test_get_text_shows_connection_details(runner, runpod_http, gpu, pod_data):
    """Print remote status and SSH alongside the saved GPU and deadline."""

    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]

    result = runner.invoke(cli, ["get", str(gpu.id)])

    assert result.exit_code == 0
    assert "Status: running" in result.stdout
    assert "Region: US-KS-2" in result.stdout
    assert "SSH: ssh root@195.26.233.3 -p 34446" in result.stdout
    assert f"Expires at: {gpu.expires_at.isoformat()}" in result.stdout


def test_get_absent_instance_is_explicit(runner, runpod_http, gpu, database_path):
    """Distinguish a missing remote instance from the saved local running state."""

    runpod_http.side_effect = [httpx.Response(404)]

    result = runner.invoke(cli, ["get", str(gpu.id)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "was not found" in result.stderr
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
