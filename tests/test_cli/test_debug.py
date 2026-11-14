import json

import httpx
import pytest

from galloc.main import cli


@pytest.mark.parametrize("mode", ["default", "flag", "env"])
def test_descriptive_errors_and_debug(runner, runpod_http, monkeypatch, mode):
    """Always show the API reason; add request diagnostics and traceback only in debug mode."""

    if mode == "env":
        monkeypatch.setenv("GALLOC_DEBUG", "true")
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
    assert "private-response" not in result.output
    assert ("Runpod request: GET catalog/datacenters" in result.stderr) == (mode != "default")
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
    assert result.stderr.count("Runpod request:") == 1
    assert result.stderr.count("Runpod response:") == 1

    result = runner.invoke(cli, ["regions", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert "DEBUG" not in result.stderr
