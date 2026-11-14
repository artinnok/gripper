import json
from datetime import timedelta

import httpx
import pytest

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME, TerminalInput


@pytest.fixture
def gpu_catalog():
    """Include varied names and prices across clouds, plus an excluded Community Cloud model."""

    return {
        "gpus": [
            make_gpu_data(
                id="NVIDIA RTX 4090",
                name="RTX 4090",
                secure=True,
                price={"secure": 0.89, "community": 0.34, "serverless": 1.34},
            ),
            make_gpu_data(id="NVIDIA H200", name="H200 SXM", secure=True, price={"secure": 5.29}),
            make_gpu_data(id="NVIDIA RTX PRO 6000", name="RTX PRO 6000", secure=True, price={"secure": 2.69}),
            make_gpu_data(id="community-gpu", name="Community only", secure=False, price={"secure": 0}),
            make_gpu_data(id="sold-out", name="Sold out", availability="NONE", dataCenters=[]),
            make_gpu_data(
                id="other-region",
                name="Another region",
                price={"secure": 99},
                dataCenters=[{"id": "EU-NL-1", "availability": "HIGH"}],
            ),
            make_gpu_data(
                id="no-regional-stock",
                name="No regional stock",
                dataCenters=[{"id": "US-KS-2", "availability": "NONE"}],
            ),
        ]
    }


@pytest.fixture
def region_catalog():
    """Supply a native placement for validating the selected GPU's creation request."""

    return {"dataCenters": [{"id": "US-KS-2", "name": "US-KS-2", "region": "NORTH_AMERICA"}]}


@pytest.mark.parametrize("output", ["text", "json"])
def test_gpu_selection(runner, runpod_http, database_path, pod_data, keyboard, gpu_catalog, region_catalog, output):
    """Show supported names, then save and submit the exact ID selected with the arrow keys."""

    runpod_http.side_effect = [
        httpx.Response(200, json=gpu_catalog),
        httpx.Response(200, json=gpu_catalog),
        httpx.Response(200, json=region_catalog),
        httpx.Response(201, json=pod_data),
    ]
    keyboard.send_bytes(b"\x1b[B\x1b[A\r")

    result = runner.invoke(
        cli, ["add", "--region", "US-KS-2", "--ttl", "30m", "--output", output], input=TerminalInput()
    )

    assert result.exit_code == 0, result.output
    assert "● H200 SXM" in result.stderr
    assert "○ RTX 4090" in result.stderr
    assert "   GPU                 USD/HOUR" in result.stderr
    assert "RTX 4090          $0.89" in result.stderr
    assert "H200 SXM          $5.29" in result.stderr
    assert "RTX PRO 6000      $2.69" in result.stderr
    assert "$0.34" not in result.stderr
    assert "$1.34" not in result.stderr
    assert "|" not in result.stderr
    assert "Community only" not in result.stderr
    assert "Sold out" not in result.stderr
    assert "Another region" not in result.stderr
    assert "No regional stock" not in result.stderr
    assert result.stderr.index("H200 SXM") < result.stderr.index("RTX PRO 6000") < result.stderr.index("RTX 4090")
    assert "VRAM (GB)" in result.stderr
    assert "RAM GB MIN" in result.stderr
    assert "vCPU MIN" in result.stderr
    assert "141        188         12" in result.stderr
    assert "Select a GPU" not in result.stdout
    assert "»" not in result.stderr
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    if output == "text":
        assert f"{gpu.name:12}  running" in result.stdout
    else:
        assert json.loads(result.stdout)["instance"]["gpu"] == "NVIDIA H200"
    _, _, _, creation_call = runpod_http.call_args_list
    (request,) = creation_call.args
    assert request.method == "POST"
    assert json.loads(request.content)["gpu"] == {"id": "NVIDIA H200", "count": 1}
    if output == "json":
        assert json.loads(result.stdout)["id"] == str(gpu.id)


def test_add_without_options(runner, runpod_http, database_path, pod_data, keyboard, gpu_catalog, region_catalog):
    """Choose a GPU across regions, then its available region and TTL through real key handling."""

    runpod_http.side_effect = [
        httpx.Response(200, json=gpu_catalog),
        httpx.Response(200, json=gpu_catalog),
        httpx.Response(200, json=region_catalog),
        httpx.Response(200, json=gpu_catalog),
        httpx.Response(200, json=region_catalog),
        httpx.Response(201, json=pod_data),
    ]
    keyboard.send_bytes(b"\x1b[B\r\r\x1b[B\r")

    result = runner.invoke(cli, ["add"], input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert result.stderr.index("Select a GPU") < result.stderr.index("Select a region")
    assert result.stderr.index("Select a region") < result.stderr.index("Select a TTL")
    assert "Another region" in result.stderr
    assert "Sold out" not in result.stderr
    assert "Community only" not in result.stderr
    assert "No regional stock" not in result.stderr
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert f"{gpu.name:12}  running" in result.stdout
    assert gpu.expires_at - CREATION_TIME == timedelta(hours=1)
    assert runpod_http.call_count == 6


@pytest.mark.parametrize("cancel_key", [b"\x1b", b"\x03", None])
@pytest.mark.parametrize("argument_list", [["add"], ["add", "--region", "US-KS-2"]])
def test_cancel_gpu(runner, runpod_http, database_path, keyboard, gpu_catalog, cancel_key, argument_list):
    """Cancel GPU selection on Esc, Ctrl+C, or EOF before other prompts or creation."""

    runpod_http.side_effect = [httpx.Response(200, json=gpu_catalog)]
    if cancel_key is None:
        keyboard.close()
    else:
        keyboard.send_bytes(cancel_key)

    result = runner.invoke(cli, argument_list, input=TerminalInput())

    assert result.exit_code == 1
    assert "Aborted!" in result.stderr
    assert "Select a region" not in result.stderr
    assert "Select a TTL" not in result.stderr
    assert result.stdout.strip() == ""
    runpod_http.assert_called_once()
    (request,) = runpod_http.call_args.args
    assert request.method == "GET"
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(200, json={"gpus": []}), "No GPUs currently available for runpod"),
        (
            httpx.Response(
                200,
                json={
                    "gpus": [
                        make_gpu_data(id="community-gpu", name="Community only", secure=False, price={"secure": 0})
                    ]
                },
            ),
            "No GPUs currently available for runpod",
        ),
        (httpx.Response(500), "HTTP 500"),
    ],
)
@pytest.mark.parametrize("argument_list", [["add"], ["add", "--region", "US-KS-2"]])
def test_unavailable_gpu_catalog(runner, runpod_http, database_path, response, message, argument_list):
    """Report an empty supported catalog or API failure before prompting or creating."""

    runpod_http.side_effect = [response]

    result = runner.invoke(cli, argument_list, input=TerminalInput())

    assert result.exit_code == 1
    assert message in result.stderr
    assert "Select a GPU" not in result.output
    runpod_http.assert_called_once()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_missing_gpu_without_terminal(runner, runpod_http, database_path):
    """Require an explicit GPU in scripts before opening storage or calling the API."""

    result = runner.invoke(cli, ["add", "--region", "US-KS-2", "--ttl", "30m"], input="H200 SXM\n")

    assert result.exit_code == 2
    assert "Pass --gpu" in result.stderr
    assert result.stdout == ""
    runpod_http.assert_not_called()
    assert not database_path.exists()
