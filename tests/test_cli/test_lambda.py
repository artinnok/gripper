import json

import httpx

from gripper.main import cli
from tests.conftest import SSH_FINGERPRINT, SSH_PUBLIC_KEY


def test_lambda_cli_lifecycle(runner, config_path, database_path, lambda_entry, lambda_http, public_key_path):
    """Register an SSH key and run the billable workflow with only Lambda configured."""

    config_path.write_text(
        f'[defaults]\nprovider = "lambda"\ndatabase_path = "{database_path}"\nstartup_poll_interval = 0.001\n'
        f'[providers.lambda]\napi_key = "lambda-test"\nssh_public_key = "{public_key_path}"\n'
    )
    key_list = []
    status = "booting"
    instance_id = "123abc"

    def handler(request):
        """Serve only documented endpoints, checking the launch and termination payloads."""

        nonlocal status
        assert request.headers["Authorization"] == "Bearer lambda-test"
        assert request.url.host == "cloud.lambda.com"
        path = request.url.path
        if path == "/api/v1/instance-types":
            data = {"gpu_8x_h100": lambda_entry}
        elif path == "/api/v1/ssh-keys" and request.method == "POST":
            payload = json.loads(request.content)
            assert payload["public_key"] == SSH_PUBLIC_KEY
            data = {"id": "key1", **payload}
            key_list.append(data)
        elif path == "/api/v1/ssh-keys" and request.method == "GET":
            data = key_list
        elif path == "/api/v1/ssh-keys/key1" and request.method == "DELETE":
            key_list.clear()
            data = {}
        elif path == "/api/v1/instance-operations/launch":
            assert request.method == "POST"
            payload = json.loads(request.content)
            assert payload.pop("name").startswith("gripper-")
            assert payload == {
                "instance_type_name": "gpu_8x_h100",
                "region_name": "us-west-1",
                "ssh_key_names": [key_list[0]["name"]],
            }
            data = {"instance_ids": [instance_id]}
        elif path == "/api/v1/instance-operations/terminate":
            assert request.method == "POST" and json.loads(request.content) == {"instance_ids": [instance_id]}
            status = "terminated"
            data = {"terminated_instances": [{"id": instance_id, "status": "terminating"}]}
        else:
            assert path == f"/api/v1/instances/{instance_id}" and request.method == "GET"
            data = {
                "id": instance_id,
                "status": status,
                "region": lambda_entry["regions_with_capacity_available"][0],
                "instance_type": lambda_entry["instance_type"],
                "first_healthy": "2026-11-19T04:00:00Z" if status == "active" else None,
                "ip": "192.0.2.2" if status == "active" else None,
            }
            if status == "booting":
                status = "active"
        return httpx.Response(200, json={"data": data})

    lambda_http.side_effect = handler
    for arguments in [
        ["regions"],
        ["ssh-key", "add", str(public_key_path)],
        ["ssh-key", "add", str(public_key_path)],
        ["ssh-key", "ls"],
    ]:
        result = runner.invoke(cli, arguments)
        assert result.exit_code == 0, result.output
    assert len(key_list) == 1

    result = runner.invoke(
        cli, ["add", "--gpu", "gpu_8x_h100", "--region", "us-west-1", "--budget", "17.96", "--output", "json"]
    )
    assert result.exit_code == 0, result.output
    assert "$35.92/h" in result.stderr and "30m" in result.stderr
    instance = json.loads(result.stdout)["instance"]
    assert instance["ssh_command"] == "ssh ubuntu@192.0.2.2"
    assert instance["hourly_price"] == 35.92 and instance["created_at"] is None
    for arguments in [
        ["ls"],
        ["get", "gpu-1"],
        ["extend", "gpu-1", "--ttl", "1h"],
        ["rm", "gpu-1"],
        ["ssh-key", "rm", SSH_FINGERPRINT],
    ]:
        result = runner.invoke(cli, arguments, input="y\n")
        assert result.exit_code == 0, result.output
        if arguments[0] in {"get", "extend"}:
            assert f"ssh -i {public_key_path.with_suffix('')} ubuntu@192.0.2.2" in result.stdout
    assert not key_list
    assert runner.invoke(cli, ["ls"]).stdout == "No GPUs.\n"
