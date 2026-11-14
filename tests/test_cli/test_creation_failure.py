import httpx

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data


def test_uncertain_creation_directs_to_provider(runner, runpod_http, database_path):
    """Identify an unresolved Pod in the provider console without retrying creation."""

    runpod_http.side_effect = [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
        ),
        httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        ),
        httpx.Response(500),
    ]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m", "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id is None
    assert gpu.name in result.stderr
    assert f"Check galloc-{gpu.id} in the runpod console." in result.stderr
    request_list = [arguments.args for arguments in runpod_http.call_args_list]
    assert [request.method for (request,) in request_list] == ["GET", "GET", "POST"]


def test_delete_requires_known_provider_id(runner, runpod_http, gpu, database_path):
    """Require a known provider ID before submitting a deletion request."""

    pending = gpu.model_copy(update={"provider_instance_id": None})
    with storage.open_database(database_path) as connection:
        storage.update_gpu_instance(connection, pending)

    result = runner.invoke(cli, ["rm", str(gpu.id), "--yes"])

    assert result.exit_code == 1
    assert f"Check galloc-{gpu.id} in the runpod console." in result.stderr
    runpod_http.assert_not_called()
