import json

import httpx

from galloc.main import cli
from galloc.settings import load_settings


def test_vast_only_configuration_and_cli_lifecycle(
    runner, config_path, database_path, vast_http, vast_offer_data, vast_instance_data, public_key_path
):
    """Create, inspect, extend, and delete through Click using only Vast.ai TOML credentials."""

    config_path.write_text(
        f'[defaults]\nprovider = "vast"\ndatabase_path = "{database_path}"\ndisk_gb = 64\n'
        f'ssh_public_key = "{public_key_path}"\n'
        '[providers.vast]\napi_key = "vast-token"\ndisk_gb = 80\n'
    )
    deleted = False

    def handler(request):
        """Serve catalog, account keys, creation, readiness, and confirmed absence."""

        nonlocal deleted
        assert request.headers["Authorization"] == "Bearer vast-token"
        if request.url.path.endswith("bundles/"):
            assert json.loads(request.content)["disk_space"] == {"gte": 80}
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        if request.url.path.endswith("ssh/"):
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"success": True})
        if request.method == "PUT":
            assert json.loads(request.content)["disk"] == 80
            return httpx.Response(200, json={"success": True, "new_contract": 5678})
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"instances": None if deleted else vast_instance_data})

    vast_http.side_effect = handler
    assert load_settings(config_path).runpod_api_key is None
    result = runner.invoke(cli, ["add", "--gpu", "1234", "--region", "US", "--budget", "2.5", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provider"] == "vast"
    for argument_list in [["get", "gpu-1"], ["extend", "gpu-1", "--ttl", "1h"], ["rm", "gpu-1"]]:
        result = runner.invoke(cli, argument_list, input="y\n")
        assert result.exit_code == 0, result.output
    assert deleted
    assert runner.invoke(cli, ["ls"]).stdout == "No GPUs.\n"
    result = runner.invoke(cli, ["providers", "--output", "json"])
    assert json.loads(result.stdout) == [
        {"name": "runpod", "configured": False},
        {"name": "vast", "configured": True},
        {"name": "lambda", "configured": False},
    ]
