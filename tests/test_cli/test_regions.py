import json

import httpx
import pytest

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data


@pytest.fixture
def data_center_list():
    """Provide native provider IDs and names from several geographic locations."""

    return [
        {"id": "EU-NL-1", "name": "EU Netherlands 1", "region": "EUROPE"},
        {"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"},
        {"id": "CA-MTL-1", "name": "CA Montreal 1", "region": "NORTH_AMERICA"},
        {"id": "AP-JP-1", "name": "AP Japan 1", "region": "ASIA"},
    ]


def test_regions_text(runner, runpod_http, database_path, data_center_list):
    """List the provider catalog without creating resources or a local database."""

    runpod_http.side_effect = [httpx.Response(200, json={"dataCenters": data_center_list})]

    result = runner.invoke(cli, ["regions", "--provider", "runpod"])

    assert result.exit_code == 0, result.output
    assert "ID              LOCATION" in result.stdout
    assert "NAME" not in result.stdout
    for data_center in data_center_list:
        assert data_center["id"] in result.stdout
        assert data_center["name"] not in result.stdout
    assert "North America" in result.stdout
    assert result.stderr == ""
    runpod_http.assert_called_once()
    (request,) = runpod_http.call_args.args
    assert (request.method, request.url.path) == ("GET", "/v2/catalog/datacenters")
    assert not database_path.exists()


def test_regions_json(runner, runpod_http, data_center_list):
    """Return one JSON array with native identifiers and readable location names."""

    runpod_http.side_effect = [httpx.Response(200, json={"dataCenters": data_center_list})]

    result = runner.invoke(cli, ["regions", "--provider", "runpod", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {"id": "EU-NL-1", "name": "EU Netherlands 1", "location": "Europe"},
        {"id": "US-KS-2", "name": "US Kansas 2", "location": "North America"},
        {"id": "CA-MTL-1", "name": "CA Montreal 1", "location": "North America"},
        {"id": "AP-JP-1", "name": "AP Japan 1", "location": "Asia"},
    ]
    assert result.stderr == ""


@pytest.mark.parametrize("output, expected", [("text", "No regions available.\n"), ("json", "[]\n")])
def test_empty_region_catalog(runner, runpod_http, output, expected):
    """Represent an empty provider catalog without prompting for input."""

    runpod_http.side_effect = [httpx.Response(200, json={"dataCenters": []})]

    result = runner.invoke(cli, ["regions", "--output", output])

    assert result.exit_code == 0
    assert result.stdout == expected


@pytest.mark.parametrize(
    "region, expected_id",
    [
        ("eu-nl-1", "EU-NL-1"),
        (" EU-NL-1 ", "EU-NL-1"),
        ("US-KS-2", "US-KS-2"),
        ("CA-MTL-1", "CA-MTL-1"),
        ("AP-JP-1", "AP-JP-1"),
    ],
)
def test_add_places_one_pod_in_selected_region(
    runner, runpod_http, database_path, pod_data, data_center_list, region, expected_id
):
    """Validate a native region and send exactly that data center in the creation request."""

    pod_data["dataCenterId"] = expected_id
    runpod_http.side_effect = [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200 SXM", secure=True, price={"secure": 5.29})]}
        ),
        httpx.Response(200, json={"dataCenters": data_center_list}),
        httpx.Response(201, json=pod_data),
    ]

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--region", region, "--ttl", "30m", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["instance"]["gpu"] == "NVIDIA H200"
    assert data["instance"]["region"] == expected_id
    assert "parameters" not in data
    _, _, creation_call = runpod_http.call_args_list
    (request,) = creation_call.args
    assert request.method == "POST"
    assert json.loads(request.content)["dataCenterIds"] == [expected_id]
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id == pod_data["id"]

    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]
    result = runner.invoke(cli, ["get", data["id"]])
    assert result.exit_code == 0
    assert f"Region: {expected_id}" in result.stdout


@pytest.mark.parametrize("region", ["eu", "us", "missing", ""])
def test_unknown_region_does_not_create_or_save(runner, runpod_http, database_path, data_center_list, region):
    """Reject unknown IDs before creation and explain how to discover valid choices."""

    runpod_http.side_effect = [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
        ),
        httpx.Response(200, json={"dataCenters": data_center_list}),
    ]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", region, "--ttl", "30m"])

    assert result.exit_code == 1
    assert "Unknown Runpod region" in result.stderr
    assert "galloc regions --provider runpod" in result.stderr
    for arguments in runpod_http.call_args_list:
        (request,) = arguments.args
        assert request.method == "GET"
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_regions_requires_credentials(runner, runpod_http, write_config):
    """Explain how to configure the selected provider before requesting its catalog."""

    write_config(runpod={"api_key": ""})
    result = runner.invoke(cli, ["regions"])

    assert result.exit_code == 1
    assert "Set api_key in [providers.runpod]" in result.stderr
    runpod_http.assert_not_called()


@pytest.mark.parametrize("status_code", [401, 500])
def test_regions_api_failure(runner, runpod_http, status_code):
    """Report catalog failures on stderr and leave JSON stdout empty."""

    runpod_http.side_effect = [httpx.Response(status_code)]

    result = runner.invoke(cli, ["regions", "--output", "json"])

    assert result.exit_code == 1
    assert f"HTTP {status_code}" in result.stderr
    assert result.stdout == ""


def test_regions_invalid_catalog(runner, runpod_http):
    """Validate region names at the API boundary instead of inventing missing labels."""

    runpod_http.side_effect = [httpx.Response(200, json={"dataCenters": [{"id": "EU-NL-1", "region": "EUROPE"}]})]

    result = runner.invoke(cli, ["regions", "--output", "json"])

    assert result.exit_code == 1
    assert "invalid response" in result.stderr
    assert result.stdout == ""
