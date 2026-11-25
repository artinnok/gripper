import httpx
import pytest

from gripper.core import storage
from gripper.main import cli
from tests.conftest import make_gpu_data


@pytest.mark.parametrize("stock", ["NONE", "another-region"])
def test_unavailable_gpu_fails_before_creation(runner, runpod_http, database_path, stock):
    """Reject an explicit GPU without local intent or a POST when regional stock is absent."""

    region_id = "US-KS-2" if stock == "NONE" else "EU-NL-1"
    availability = "NONE" if stock == "NONE" else "HIGH"
    runpod_http.side_effect = [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(dataCenters=[{"id": region_id, "availability": availability}])]}
        ),
        httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        ),
    ]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"])

    assert result.exit_code == 1
    assert "H200 is currently unavailable in US-KS-2" in result.stderr
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_sold_out_gpu_fails_before_creation(runner, runpod_http, database_path):
    """Explain missing global capacity instead of attempting an unavailable model."""

    runpod_http.side_effect = [httpx.Response(200, json={"gpus": [make_gpu_data(availability="NONE", dataCenters=[])]})]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"])

    assert result.exit_code == 1
    assert "H200 is currently unavailable on Runpod" in result.stderr
    runpod_http.assert_called_once()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
