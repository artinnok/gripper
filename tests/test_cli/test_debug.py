import json
import logging

import httpx
import pytest

from gripper.main import cli


@pytest.mark.parametrize("mode", ["default", "flag", "config"])
def test_descriptive_errors_and_debug(runner, runpod_http, write_config, mode):
    """Always show the API reason; add request diagnostics and traceback only in debug mode."""

    if mode == "config":
        write_config(defaults={"debug": True})
    argument_list = ["regions", "--output", "json"]
    if mode == "flag":
        argument_list.insert(0, "--debug")
    runpod_http.side_effect = [
        httpx.Response(
            403,
            json={
                "title": "Forbidden",
                "detail": "Catalog access denied for test-token",
                "status": 403,
                "errors": ["Enable Catalog Read."],
                "internal": "private-response",
            },
        )
    ]

    result = runner.invoke(cli, argument_list)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Catalog access denied for [REDACTED]" in result.stderr
    assert "Enable Catalog Read." in result.stderr
    assert "test-token" not in result.output
    assert ("private-response" in result.stderr) == (mode != "default")
    assert ("REQUEST GET https://runpod.test/v2/catalog/datacenters" in result.stderr) == (mode != "default")
    assert "HTTP 403" in result.stderr
    assert ("Traceback" in result.stderr) == (mode != "default")


def test_debug_preserves_json_and_does_not_leak_between_invocations(runner, runpod_http):
    """Keep diagnostics on stderr and remove their handler when the command exits."""

    runpod_http.side_effect = [
        httpx.Response(200, json={"dataCenters": []}),
        httpx.Response(200, json={"dataCenters": []}),
    ]

    result = runner.invoke(cli, ["--debug", "regions", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert result.stderr.count("REQUEST GET") == 1
    assert result.stderr.count("RESPONSE GET") == 1

    result = runner.invoke(cli, ["regions", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert "DEBUG" not in result.stderr


def test_failed_command_restores_logging_before_next_invocation(runner, runpod_http):
    """Release the debug handler after an error and avoid leaking it into the next command."""

    logger = logging.getLogger("gripper")
    original_handlers = logger.handlers[:]
    original_level, original_propagate = logger.level, logger.propagate
    runpod_http.side_effect = [
        httpx.Response(500, json={"detail": "Temporary provider failure"}),
        httpx.Response(200, json={"dataCenters": []}),
    ]

    failed = runner.invoke(cli, ["--debug", "regions", "--output", "json"])
    assert failed.exit_code == 1 and failed.stdout == ""
    assert "Traceback" in failed.stderr
    assert logger.handlers == original_handlers
    assert (logger.level, logger.propagate) == (original_level, original_propagate)

    succeeded = runner.invoke(cli, ["regions", "--output", "json"])
    assert succeeded.exit_code == 0 and json.loads(succeeded.stdout) == []
    assert succeeded.stderr == ""
