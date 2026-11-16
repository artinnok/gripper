import json

import httpx
import pytest

from galloc.main import cli
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_PUBLIC_KEY
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize(
    "provider_values, expected_disk, expected_key",
    [
        ({}, 100, SSH_PUBLIC_KEY),
        ({"disk_gb": 80, "ssh_public_key": "override.pub"}, 80, OTHER_SSH_PUBLIC_KEY),
    ],
)
def test_add_uses_configured_disk_and_key(
    runner, write_config, public_key_path, tmp_path, runpod_http, pod_data, provider_values, expected_disk, expected_key
):
    """Apply shared defaults and provider overrides to actual registration and creation requests."""

    (tmp_path / "override.pub").write_text(OTHER_SSH_PUBLIC_KEY)
    write_config(
        defaults={"provider": "runpod", "disk_gb": 100, "ssh_public_key": str(public_key_path)},
        runpod=provider_values,
    )

    def handler(request):
        """Answer configuration-driven SSH registration and normal GPU creation."""

        if request.url.path.endswith("account/ssh-keys"):
            return httpx.Response(200, json={"keys": [] if request.method == "GET" else [expected_key]})
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler
    result = runner.invoke(cli, ["add", "--gpu", "H200", "--region", "US-KS-2", "--ttl", "1h", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "running"
    (key_request,) = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "PUT"]
    (pod_request,) = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "POST"]
    assert json.loads(key_request.content) == {"keys": [expected_key]}
    assert json.loads(pod_request.content)["disk"] == expected_disk


def test_ssh_key_add_uses_config_or_explicit_argument(runner, write_config, public_key_path, runpod_http, tmp_path):
    """Use the saved key by default and let an explicit file override it."""

    write_config(defaults={"provider": "runpod", "ssh_public_key": str(public_key_path)})
    explicit_key = tmp_path / "explicit.pub"
    explicit_key.write_text(OTHER_SSH_PUBLIC_KEY)
    runpod_http.side_effect = lambda request: httpx.Response(200, json={"keys": []})

    for argument_list, expected_key in [([], SSH_PUBLIC_KEY), ([str(explicit_key)], OTHER_SSH_PUBLIC_KEY)]:
        result = runner.invoke(cli, ["ssh-key", "add", *argument_list])
        assert result.exit_code == 0, result.output
        assert json.loads(runpod_http.call_args.args[0].content) == {"keys": [expected_key]}


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
