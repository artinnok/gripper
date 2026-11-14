import json

import httpx
import pytest

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import TerminalInput


@pytest.fixture
def data_center_list():
    """Provide two choices, including a catalog name that repeats its ID."""

    return [
        {"id": "EU-NL-1", "name": "EU-NL-1", "region": "EUROPE"},
        {"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"},
        {"id": "CA-MTL-1", "name": "CA-MTL-1", "region": "NORTH_AMERICA"},
        {"id": "AP-JP-1", "name": "AP-JP-1", "region": "ASIA"},
    ]


@pytest.fixture
def gpu_response():
    """Provide stock in two regions, an unavailable region, and an omitted region."""

    return httpx.Response(
        200,
        json={
            "gpus": [
                make_gpu_data(
                    name="H200 SXM",
                    dataCenters=[
                        {"id": "EU-NL-1", "availability": "HIGH"},
                        {"id": "US-KS-2", "availability": "LOW"},
                        {"id": "CA-MTL-1", "availability": "NONE"},
                    ],
                )
            ]
        },
    )


@pytest.mark.parametrize("output", ["text", "json"])
@pytest.mark.parametrize("keys", [b"\x1b[B\r", b"\x1b[B\x1b[A\x1b[B\r"])
def test_interactive_region_selection(
    runner, runpod_http, database_path, pod_data, data_center_list, gpu_response, keyboard, output, keys
):
    """Validate the selected region and keep prompts out of the command's result stream."""

    catalog = {"dataCenters": data_center_list}
    runpod_http.side_effect = [
        gpu_response,
        httpx.Response(200, json=catalog),
        gpu_response,
        httpx.Response(200, json=catalog),
        httpx.Response(201, json=pod_data),
    ]

    keyboard.send_bytes(keys)
    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--ttl", "30m", "--output", output], input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert "   ID                LOCATION" in result.stderr
    assert "● EU-NL-1" in result.stderr
    assert "○ US-KS-2" in result.stderr
    assert "»" not in result.stderr
    assert "EU-NL-1         Europe" in result.stderr
    assert "US-KS-2         North America" in result.stderr
    assert "US Kansas 2" not in result.stderr
    assert "CA-MTL-1" not in result.stderr
    assert "AP-JP-1" not in result.stderr
    assert "Select a GPU" not in result.stderr
    assert "Select a region" not in result.stdout
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id == pod_data["id"]
    _, _, _, _, creation_call = runpod_http.call_args_list
    (request,) = creation_call.args
    assert request.method == "POST"
    assert json.loads(request.content)["dataCenterIds"] == ["US-KS-2"]
    if output == "json":
        assert json.loads(result.stdout)["id"] == str(gpu.id)
    else:
        assert "running     US-KS-2" in result.stdout


def test_explicit_region_skips_picker(runner, runpod_http, pod_data, data_center_list):
    """Preserve explicit region selection even when stdin is a terminal."""

    runpod_http.side_effect = [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200 SXM", secure=True, price={"secure": 5.29})]}
        ),
        httpx.Response(200, json={"dataCenters": data_center_list}),
        httpx.Response(201, json=pod_data),
    ]

    result = runner.invoke(
        cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2", "--ttl", "30m"], input=TerminalInput()
    )

    assert result.exit_code == 0, result.output
    assert "Select a GPU" not in result.output
    assert "Select a region" not in result.output
    assert runpod_http.call_count == 3


def test_missing_region_without_terminal(runner, runpod_http, database_path):
    """Fail before HTTP or database access instead of reading a selection from a pipe."""

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--ttl", "30m"], input="2\n")

    assert result.exit_code == 2
    assert "Pass --region" in result.stderr
    assert result.stdout == ""
    runpod_http.assert_not_called()
    assert not database_path.exists()


@pytest.mark.parametrize("cancel_key", [b"\x1b", b"\x03", None])
def test_cancelled_selection_does_not_create(
    runner, runpod_http, database_path, data_center_list, gpu_response, keyboard, cancel_key
):
    """Cancel on Esc, Ctrl+C, or EOF without saving intent or sending a creation request."""

    runpod_http.side_effect = [gpu_response, httpx.Response(200, json={"dataCenters": data_center_list})]
    if cancel_key is None:
        keyboard.close()
    else:
        keyboard.send_bytes(cancel_key)

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--ttl", "30m"], input=TerminalInput())

    assert result.exit_code == 1
    assert "Aborted!" in result.stderr
    assert result.stdout.strip() == ""
    assert runpod_http.call_count == 2
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(200, json={"dataCenters": []}), "No regions currently available for H200 SXM on runpod"),
        (
            httpx.Response(
                200, json={"dataCenters": [{"id": "CA-MTL-1", "name": "CA-MTL-1", "region": "NORTH_AMERICA"}]}
            ),
            "No regions currently available for H200 SXM on runpod",
        ),
        (httpx.Response(500), "HTTP 500"),
    ],
)
def test_catalog_failure_before_prompt(runner, runpod_http, database_path, gpu_response, response, message):
    """Do not prompt or create when the provider cannot supply region choices."""

    runpod_http.side_effect = [gpu_response, response]

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--ttl", "30m"], input=TerminalInput(b"1\n"))

    assert result.exit_code == 1
    assert message in result.stderr
    assert "Select a region" not in result.output
    assert runpod_http.call_count == 2
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
