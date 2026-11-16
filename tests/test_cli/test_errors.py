from uuid import uuid4

import httpx
import pytest

from galloc.core import storage
from galloc.main import cli


@pytest.mark.parametrize(
    "argument_list",
    [
        ["get", "invalid-id"],
        ["add", "--gpu", "H200", "--ttl", "30m"],
        ["add", "--provider", "unknown", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m"],
        ["ls", "--output", "xml"],
        ["delete", "gpu-1"],
    ],
)
def test_usage_errors_before_http(runner, runpod_http, argument_list):
    """Reject malformed command inputs before attempting a remote operation."""

    result = runner.invoke(cli, argument_list)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error:" in result.stderr
    runpod_http.assert_not_called()


def test_invalid_ttl_before_creation(runner, runpod_http, database_path):
    """Reject an invalid deadline without catalog requests or a saved creation intent."""

    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "0m"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    runpod_http.assert_not_called()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_missing_credentials(runner, write_config, runpod_http, gpu):
    """Explain how to configure a remote operation without a traceback."""

    write_config(runpod={"api_key": ""})

    result = runner.invoke(cli, ["get", str(gpu.id)])

    assert result.exit_code == 1
    assert "Set api_key in [providers.runpod]" in result.stderr
    assert result.stdout == ""
    runpod_http.assert_not_called()


def test_missing_gpu(runner, runpod_http):
    """Fail clearly for an unknown local ID before opening a provider."""

    result = runner.invoke(cli, ["get", str(uuid4())])

    assert result.exit_code == 1
    assert "not found" in result.stderr
    runpod_http.assert_not_called()


def test_invalid_settings_do_not_echo_values(runner, write_config, config_path):
    """Identify the invalid setting without including its potentially sensitive input."""

    write_config(defaults={"api_timeout": "private-input"})

    result = runner.invoke(cli, ["providers"])

    assert result.exit_code == 1
    assert "api_timeout" in result.stderr
    assert str(config_path) in result.stderr
    assert ".env" not in result.stderr
    assert "private-input" not in result.output


def test_invalid_config_reports_the_file_without_echoing_credentials(runner, config_path, runpod_http):
    """Explain a TOML syntax error before accessing the provider."""

    config_path.write_text('[providers.runpod]\napi_key = "private-input')

    result = runner.invoke(cli, ["providers"])

    assert result.exit_code == 1
    assert "Invalid TOML" in result.stderr
    assert str(config_path) in result.stderr
    assert "private-input" not in result.output
    runpod_http.assert_not_called()


def test_storage_error_is_readable(runner, write_config, tmp_path):
    """Translate an unusable database directory into a normal CLI error."""

    parent_path = tmp_path / "file"
    parent_path.write_text("not a directory")
    write_config(defaults={"database_path": str(parent_path / "state.db")})

    result = runner.invoke(cli, ["ls"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_api_failure_preserves_stdout(runner, runpod_http, gpu):
    """Keep failed JSON commands machine-readable by sending errors only to stderr."""

    runpod_http.side_effect = [httpx.Response(500, text="test-token")]

    result = runner.invoke(cli, ["get", str(gpu.id), "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "HTTP 500" in result.stderr
    assert "test-token" not in result.output


def test_failed_deletion_can_be_retried(runner, runpod_http, gpu, database_path):
    """Keep deletion pending after an API failure and allow an explicit retry."""

    runpod_http.side_effect = [httpx.Response(500), httpx.Response(204)]

    result = runner.invoke(cli, ["rm", str(gpu.id), "--yes"])

    assert result.exit_code == 1
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu(connection, gpu.id) == gpu

    result = runner.invoke(cli, ["rm", str(gpu.id), "--yes"])

    assert result.exit_code == 0
    assert result.stdout == f"{gpu.name}\n"
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize("exception", [ValueError("Unexpected value"), TypeError("Unexpected type")])
def test_programming_errors_are_not_translated(runner, runpod_http, exception):
    """Preserve unexpected exceptions instead of presenting them as expected application failures."""

    runpod_http.side_effect = exception

    result = runner.invoke(cli, ["regions", "--output", "json"])

    assert result.exit_code == 1
    assert result.exception is exception
    assert result.stdout == ""
    assert "Error:" not in result.stderr
