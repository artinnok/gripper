import json

import httpx
import pytest

from galloc.main import cli
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_FINGERPRINT, SSH_PUBLIC_KEY


@pytest.mark.parametrize("existing_key_list", [[], [OTHER_SSH_PUBLIC_KEY]])
def test_add_preserves_existing_keys(runner, runpod_http, database_path, public_key_path, existing_key_list):
    """Register the supplied key without overwriting another key or creating local state."""

    expected_key_list = [*existing_key_list, SSH_PUBLIC_KEY]
    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": existing_key_list}),
        httpx.Response(200, json={"keys": expected_key_list}),
    ]

    result = runner.invoke(cli, ["ssh-key", "add", str(public_key_path), "--provider", "runpod"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"Registered  {SSH_FINGERPRINT}\n"
    read_call, write_call = runpod_http.call_args_list
    (read_request,) = read_call.args
    (write_request,) = write_call.args
    assert (read_request.method, read_request.url.path) == ("GET", "/v2/account/ssh-keys")
    assert (write_request.method, write_request.url.path) == ("PUT", "/v2/account/ssh-keys")
    assert read_request.headers["Authorization"] == "Bearer test-token"
    assert write_request.headers["Authorization"] == "Bearer test-token"
    assert json.loads(write_request.content) == {"keys": expected_key_list}
    assert public_key_path.read_text() == SSH_PUBLIC_KEY + "\n"
    assert not database_path.exists()


def test_duplicate_registration_ignores_comment(runner, runpod_http, public_key_path):
    """Identify a key by its fingerprint and avoid rewriting the same registration."""

    runpod_http.side_effect = [httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY.replace("laptop", "old comment")]})]

    result = runner.invoke(cli, ["ssh-key", "add", str(public_key_path), "--provider", "runpod", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"fingerprint": SSH_FINGERPRINT, "created": False}
    runpod_http.assert_called_once()
    (request,) = runpod_http.call_args.args
    assert request.method == "GET"


@pytest.mark.parametrize("public_key", [SSH_PUBLIC_KEY, SSH_PUBLIC_KEY.removesuffix(" laptop")])
def test_add_json(runner, runpod_http, public_key_path, public_key):
    """Accept a key with or without a comment and emit one JSON result."""

    public_key_path.write_text(public_key + "\n")
    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": []}),
        httpx.Response(200, json={"keys": [public_key]}),
    ]

    result = runner.invoke(cli, ["ssh-key", "add", str(public_key_path), "--provider", "runpod", "--output", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"fingerprint": SSH_FINGERPRINT, "created": True}
    assert result.stderr == ""


@pytest.mark.parametrize("output", ["text", "json"])
def test_list_reads_current_provider_state(runner, runpod_http, database_path, output):
    """Show current keys and reflect an external removal on the next invocation."""

    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY]}),
        httpx.Response(200, json={"keys": []}),
    ]
    argument_list = ["ssh-key", "ls", "--provider", "runpod", "--output", output]

    result = runner.invoke(cli, argument_list)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    if output == "json":
        assert json.loads(result.stdout) == [
            {"public_key": SSH_PUBLIC_KEY, "fingerprint": SSH_FINGERPRINT, "comment": "laptop"}
        ]
    else:
        header, row = result.stdout.splitlines()
        assert header.startswith("FINGERPRINT")
        assert row.startswith(SSH_FINGERPRINT)
        assert header.index("COMMENT") == row.index("laptop")

    result = runner.invoke(cli, argument_list)
    assert result.exit_code == 0, result.output
    assert result.stdout == ("[]\n" if output == "json" else "No SSH keys registered.\n")
    assert runpod_http.call_count == 2
    assert not database_path.exists()


@pytest.mark.parametrize("remaining_key_list", [[], [OTHER_SSH_PUBLIC_KEY]])
def test_rm_preserves_other_keys_and_can_repeat(
    runner, runpod_http, database_path, public_key_path, remaining_key_list
):
    """Remove one registration, retain unrelated keys, and treat repeated removal as absent."""

    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY, *remaining_key_list]}),
        httpx.Response(200, json={"keys": remaining_key_list}),
        httpx.Response(200, json={"keys": remaining_key_list}),
    ]
    argument_list = ["ssh-key", "rm", SSH_FINGERPRINT, "--provider", "runpod", "--yes", "--output", "json"]

    result = runner.invoke(cli, argument_list)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"fingerprint": SSH_FINGERPRINT, "removed": True}
    assert result.stderr == ""
    read_call, write_call = runpod_http.call_args_list
    (request,) = write_call.args
    assert (request.method, request.url.path) == ("PUT", "/v2/account/ssh-keys")
    assert json.loads(request.content) == {"keys": remaining_key_list}

    result = runner.invoke(cli, argument_list)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"fingerprint": SSH_FINGERPRINT, "removed": False}
    assert runpod_http.call_count == 3
    assert public_key_path.read_text() == SSH_PUBLIC_KEY + "\n"
    assert not database_path.exists()


def test_rm_confirmation(runner, runpod_http):
    """Ask before removal and make no request when the user cancels."""

    argument_list = ["ssh-key", "rm", SSH_FINGERPRINT, "--provider", "runpod"]
    result = runner.invoke(cli, argument_list, input="n\n")
    assert result.exit_code == 1
    assert "Aborted!" in result.stderr
    runpod_http.assert_not_called()

    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY]}),
        httpx.Response(200, json={"keys": []}),
    ]
    result = runner.invoke(cli, argument_list, input="y\n")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"Removed  {SSH_FINGERPRINT}\n"
    assert f"Remove SSH key {SSH_FINGERPRINT} from runpod?" in result.stderr
