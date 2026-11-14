import json
from uuid import uuid4

import httpx
import pytest

from galloc.core import storage
from galloc.main import cli


@pytest.mark.parametrize("command", ["get", "ls"])
@pytest.mark.parametrize("output", ["text", "json"])
@pytest.mark.parametrize("absence", ["missing", "terminated"])
def test_provider_changes_are_visible_without_local_sync(
    runner, runpod_http, gpu, database_path, pod_data, command, output, absence
):
    """Read live state and discard externally removed Pods instead of keeping history."""

    argument_list = [command, gpu.name] if command == "get" else [command]
    with storage.open_database(database_path) as connection:
        before = dict(connection.execute("SELECT * FROM gpus").fetchone())
    absent_response = httpx.Response(404)
    if absence == "terminated":
        absent_response = httpx.Response(200, json=pod_data | {"status": "TERMINATED"})
    runpod_http.side_effect = [
        httpx.Response(200, json=pod_data),
        httpx.Response(200, json=pod_data | {"status": "EXITED"}),
        absent_response,
    ]
    for expected in ["running", "stopped"]:
        result = runner.invoke(cli, [*argument_list, "--output", output])
        assert result.exit_code == 0, result.output
        if output == "json":
            data = json.loads(result.stdout)
            if command == "ls":
                (data,) = data
            assert data["status"] == expected
            assert "parameters" not in data
            assert "created_at" not in data
        else:
            assert expected in result.stdout
        with storage.open_database(database_path) as connection:
            assert dict(connection.execute("SELECT * FROM gpus").fetchone()) == before

    result = runner.invoke(cli, [*argument_list, "--output", output])
    if command == "get":
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "was not found" in result.stderr
    else:
        assert result.exit_code == 0
        assert result.stdout == ("[]\n" if output == "json" else "No GPUs.\n")
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []
    result = runner.invoke(cli, ["ls", "--output", "json"])
    assert json.loads(result.stdout) == []
    assert runpod_http.call_count == 3
    assert all(arguments.args[0].method == "GET" for arguments in runpod_http.call_args_list)


@pytest.mark.parametrize("command", ["get", "ls"])
@pytest.mark.parametrize(
    "failure", [httpx.Response(401), httpx.Response(403), httpx.Response(500), httpx.ConnectError("offline")]
)
def test_api_failure_never_looks_like_deletion_or_cached_success(
    runner, runpod_http, gpu, database_path, command, failure
):
    """Surface failures with no invented status and no partial JSON."""

    argument_list = [command, gpu.name] if command == "get" else [command]
    runpod_http.side_effect = [failure]
    result = runner.invoke(cli, [*argument_list, "--output", "json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error:" in result.stderr
    assert "deleted" not in result.stderr
    assert "running" not in result.stderr
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id) == gpu


def test_extend_rejects_externally_deleted_pod(runner, runpod_http, gpu, database_path):
    """Discard a confirmed obsolete mapping instead of extending its deadline."""

    runpod_http.side_effect = [httpx.Response(404)]
    result = runner.invoke(cli, ["extend", gpu.name, "--ttl", "1h"])
    assert result.exit_code == 1
    assert "was not found" in result.stderr
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_gpu_region_and_created_at_come_from_api(runner, runpod_http, gpu, pod_data):
    """Display updated remote hardware and location instead of creation-time copies."""

    pod_data.update(gpu={"id": "NVIDIA RTX 4090"}, dataCenterId="EU-NL-1", createdAt="2026-11-13T10:01:00Z")
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]
    result = runner.invoke(cli, ["get", gpu.name, "--output", "json"])
    assert result.exit_code == 0
    instance = json.loads(result.stdout)["instance"]
    assert instance["gpu"] == "NVIDIA RTX 4090"
    assert instance["region"] == "EU-NL-1"
    assert instance["created_at"] == "2026-11-13T10:01:00Z"


def test_listing_removes_only_confirmed_absent_mappings(runner, runpod_http, gpu, database_path, pod_data):
    """Omit an absent Pod while keeping the other existing GPU and its deadline."""

    remaining = gpu.model_copy(update={"id": uuid4(), "number": None, "provider_instance_id": "remaining"})
    pending = gpu.model_copy(update={"id": uuid4(), "number": None, "provider_instance_id": None})
    with storage.open_database(database_path) as connection:
        storage.create_gpu(connection, remaining)
        storage.create_gpu(connection, pending)
    runpod_http.side_effect = [httpx.Response(404), httpx.Response(200, json=pod_data | {"id": "remaining"})]

    result = runner.invoke(cli, ["ls", "--output", "json"])

    assert result.exit_code == 0, result.output
    (data,) = json.loads(result.stdout)
    assert data["name"] == "gpu-2"
    assert data["status"] == "running"
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == [remaining, pending]
    assert runpod_http.call_count == 2
