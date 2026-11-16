from datetime import timedelta

import httpx
import pytest

from galloc.core import storage
from galloc.core.schemas import GPU
from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME, TerminalInput


@pytest.fixture
def creation_response_list(pod_data):
    """Quote a GPU at three dollars per hour and accept one Pod creation."""

    return [
        httpx.Response(200, json={"gpus": [make_gpu_data(price={"secure": 3})]}),
        httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        ),
        httpx.Response(201, json=pod_data),
    ]


@pytest.mark.parametrize("interactive", [False, True])
def test_budget_creates_with_calculated_ttl(runner, runpod_http, database_path, creation_response_list, interactive):
    """Accept a fractional USD budget and persist its deadline without a TTL prompt."""

    runpod_http.side_effect = creation_response_list
    result = runner.invoke(
        cli,
        ["add", "--gpu", "H200", "--region", "US-KS-2", "--budget", "2.5", "--output", "json"],
        input=TerminalInput() if interactive else None,
    )

    assert result.exit_code == 0, result.output
    gpu = GPU.model_validate_json(result.stdout)
    assert gpu.expires_at - CREATION_TIME == timedelta(minutes=50)
    assert "budget $2.5" in result.stderr
    assert "Compute  ~$2.50 · $3.00/h · 50m" in result.stderr
    assert "Select a TTL" not in result.stderr
    assert "no spending cap" in result.stderr
    assert result.stderr.index("Compute  ") < result.stderr.index("Creating…")
    assert runpod_http.call_count == 3
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id) == gpu


@pytest.mark.parametrize("interactive", [False, True])
def test_ttl_shows_estimated_cost(runner, runpod_http, creation_response_list, interactive):
    """Show the reverse estimate without requiring a budget or extra API calls."""

    runpod_http.side_effect = creation_response_list
    result = runner.invoke(
        cli,
        ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"],
        input=TerminalInput() if interactive else None,
    )

    assert result.exit_code == 0, result.output
    assert "Compute  ~$1.50 · $3.00/h · 30m" in result.stderr
    assert "disk/tax extra" in result.stderr
    assert runpod_http.call_count == 3


@pytest.mark.parametrize("budget", ["invalid", "0", "-5", "NaN", "Infinity"])
def test_invalid_budget_before_provider_access(runner, runpod_http, database_path, budget):
    """Reject invalid amounts without a creation intent or catalog requests."""

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--budget", budget])

    assert result.exit_code == 1
    assert "positive budget" in result.stderr
    runpod_http.assert_not_called()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize("budget", ["0.01", "1e20"])
def test_unusable_budget_does_not_create(runner, runpod_http, database_path, creation_response_list, budget):
    """Reject a duration outside the supported range before making a billable request."""

    gpu_response, region_response, creation_response = creation_response_list
    runpod_http.side_effect = [gpu_response, region_response]
    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--budget", budget])

    assert result.exit_code == 1
    assert "Budget" in result.stderr
    assert runpod_http.call_count == 2
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
