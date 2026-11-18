import json

import httpx
import pytest

from galloc.main import cli
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize("provider_values, expected_disk", [({}, 100), ({"disk_gb": 80}, 80)])
def test_add_uses_configured_disk_and_registers_no_key(
    runner, write_config, public_key_path, runpod_http, pod_data, provider_values, expected_disk
):
    """Apply shared defaults and provider overrides to creation and leave the account's SSH keys untouched."""

    write_config(
        defaults={"provider": "runpod", "disk_gb": 100, "ssh_public_key": str(public_key_path)},
        runpod=provider_values,
    )
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)
    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "1h", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "running"
    request_list = [call.args[0] for call in runpod_http.call_args_list]
    (pod_request,) = [request for request in request_list if request.method == "POST"]
    assert json.loads(pod_request.content)["disk"] == expected_disk
    assert not any(request.url.path.endswith("account/ssh-keys") for request in request_list)


def test_ssh_key_add_requires_the_key_file(runner, write_config, public_key_path, runpod_http):
    """Register only a key named on the command line, never one taken from the configuration."""

    write_config(defaults={"provider": "runpod", "ssh_public_key": str(public_key_path)})

    result = runner.invoke(cli, ["ssh-key", "add"])

    assert result.exit_code == 2
    assert "PUBLIC_KEY_PATH" in result.stderr
    runpod_http.assert_not_called()


def test_explicit_provider_overrides_the_configured_default(runner, write_config, runpod_http):
    """Read the configured provider unless the command explicitly chooses another one."""

    write_config(defaults={"provider": "not-configured"})
    result = runner.invoke(cli, ["regions"])
    assert result.exit_code == 1
    assert "Unsupported provider: not-configured" in result.stderr
    runpod_http.assert_not_called()

    runpod_http.side_effect = lambda request: httpx.Response(200, json={"dataCenters": []})
    result = runner.invoke(cli, ["regions", "--provider", "runpod"])
    assert result.exit_code == 0, result.output
