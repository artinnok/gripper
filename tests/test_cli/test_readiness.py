import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from gripper.core import storage
from gripper.core.schemas import GPU
from gripper.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME


@pytest.fixture
def creation_response_list(pod_data):
    """Create a Pod whose desired status is running but whose runtime is still absent."""

    return [
        httpx.Response(200, json={"gpus": [make_gpu_data()]}),
        httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        ),
        httpx.Response(201, json=pod_data | {"runtime": None}),
    ]


def test_add_waits_for_runtime_and_ssh(
    runner, runpod_http, creation_response_list, pod_data, database_path, monkeypatch
):
    """Wait through incomplete startup signals, then show readiness without persisting its status."""

    sleep = AsyncMock()
    monkeypatch.setattr("gripper.core.readiness.sleep", sleep)
    runpod_http.side_effect = [
        *creation_response_list,
        httpx.Response(200, json=pod_data | {"status": "STARTING"}),
        httpx.Response(200, json=pod_data | {"runtime": None}),
        httpx.Response(200, json=pod_data | {"runtime": {"uptime": 3, "gpus": []}}),
        httpx.Response(200, json=pod_data | {"ssh": {"direct": None, "proxy": None}}),
        httpx.Response(200, json=pod_data),
    ]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m", "--output", "json"])

    assert result.exit_code == 0, result.output
    gpu = GPU.model_validate_json(result.stdout)
    assert gpu.name == "gpu-1"
    assert json.loads(result.stdout)["status"] == "running"
    assert gpu.expires_at - CREATION_TIME == timedelta(minutes=30)
    assert json.loads(result.stdout)["instance"]["ssh_command"] == pod_data["ssh"]["direct"]["command"]
    assert "gpu-1  Preparing…" in result.stderr
    assert "\x1b" not in result.stderr
    assert sleep.call_count == 4
    assert runpod_http.call_count == 8
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, "gpu-1") == gpu


@pytest.mark.parametrize("status", ["EXITED", "ERROR", "TERMINATED"])
def test_startup_failure_keeps_resource_visible(
    runner, runpod_http, creation_response_list, pod_data, database_path, status
):
    """Report failed startup without deleting, recreating, or losing the Pod's reference."""

    runpod_http.side_effect = [*creation_response_list, httpx.Response(200, json=pod_data | {"status": status})]
    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "gripper get gpu-1" in result.stderr
    assert "gripper rm gpu-1" in result.stderr
    assert runpod_http.call_count == 4
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, "gpu-1").provider_instance_id == pod_data["id"]


@pytest.mark.parametrize("failure", [httpx.Response(404), httpx.Response(500), KeyboardInterrupt()])
def test_interrupted_wait_preserves_gpu(runner, runpod_http, creation_response_list, database_path, failure):
    """Retain the saved instance after cancellation or a failed status request."""

    runpod_http.side_effect = [*creation_response_list, failure]
    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m", "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "gripper get gpu-1" in result.stderr
    assert "gripper rm gpu-1" in result.stderr
    assert runpod_http.call_count == 4
    with storage.open_database(database_path) as connection:
        gpu = storage.get_gpu(connection, "gpu-1")
    assert gpu.provider_instance_id is not None


def test_startup_timeout(runner, runpod_http, creation_response_list, pod_data, monkeypatch, write_config):
    """Bound the wait while preserving the running resource for explicit follow-up."""

    write_config(defaults={"startup_timeout": 1})
    monkeypatch.setattr("gripper.core.readiness.monotonic", Mock(side_effect=[0, 2]))
    runpod_http.side_effect = [*creation_response_list, httpx.Response(200, json=pod_data | {"runtime": None})]

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"])

    assert result.exit_code == 1
    assert "not ready after 1 seconds" in result.stderr
    assert "gripper rm gpu-1" in result.stderr
    assert runpod_http.call_count == 4
