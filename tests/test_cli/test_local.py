import json

import pytest

from galloc.core import storage
from galloc.main import cli


@pytest.mark.parametrize("token, configured", [("test-token", True), ("", False)])
def test_providers_reports_configuration(runner, monkeypatch, runpod_http, token, configured):
    """Report configured credentials without exposing or validating them remotely."""

    monkeypatch.setenv("GALLOC_RUNPOD_API_KEY", token)

    result = runner.invoke(cli, ["providers", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"name": "runpod", "configured": configured}]
    assert "test-token" not in result.output
    runpod_http.assert_not_called()


@pytest.mark.parametrize("argument_list", [["ls"], ["get", "gpu-1"], ["extend", "gpu-1", "--ttl", "1h"]])
def test_live_commands_require_credentials(runner, monkeypatch, runpod_http, gpu, argument_list):
    """Never substitute a local status when provider credentials are missing."""

    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY")
    result = runner.invoke(cli, argument_list)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Set GALLOC_RUNPOD_API_KEY" in result.stderr
    runpod_http.assert_not_called()


def test_unresolved_creation_requires_provider_inspection(runner, monkeypatch, gpu, database_path):
    """Direct unresolved creation to the provider without presenting it as an existing GPU."""

    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY")
    pending = gpu.model_copy(update={"provider_instance_id": None})
    with storage.open_database(database_path) as connection:
        storage.update_gpu_instance(connection, pending)

    result = runner.invoke(cli, ["get", str(gpu.id), "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert f"Check galloc-{gpu.id} in the runpod console." in result.stderr
    result = runner.invoke(cli, ["ls", "--output", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id).provider_instance_id is None


def test_empty_list(runner):
    """Make an empty database understandable in text and usable in scripts."""

    result = runner.invoke(cli, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == "No GPUs.\n"
    result = runner.invoke(cli, ["ls", "--output", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize("name", ["add", "ls", "get", "extend", "rm", "providers", "regions", "ssh-key"])
def test_command_help_without_credentials(runner, monkeypatch, name):
    """Keep every public command discoverable before provider configuration."""

    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY")

    result = runner.invoke(cli, [name, "--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
