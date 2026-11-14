import httpx
import pytest

from galloc.main import cli
from tests.conftest import SSH_FINGERPRINT, SSH_PUBLIC_KEY


@pytest.mark.parametrize(
    "content",
    [
        b"-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-key-content\n-----END OPENSSH PRIVATE KEY-----",
        b"not a key",
        b"ssh-ed25519 invalid-base64 comment",
        (SSH_PUBLIC_KEY + "\n" + SSH_PUBLIC_KEY).encode(),
        b"\xff\xfe",
    ],
)
def test_invalid_key_fails_before_http(runner, runpod_http, public_key_path, content):
    """Reject private, malformed, multiple, or non-UTF-8 keys before a provider request."""

    public_key_path.write_bytes(content)
    result = runner.invoke(cli, ["--debug", "ssh-key", "add", str(public_key_path), "--provider", "runpod"])

    assert result.exit_code == 1
    assert "public key" in result.stderr
    assert "private-key-content" not in result.output
    assert result.stdout == ""
    runpod_http.assert_not_called()


@pytest.mark.parametrize("action", ["ls", "add", "rm"])
def test_provider_is_required(runner, runpod_http, public_key_path, action):
    """Require the destination explicitly for every SSH key action."""

    argument_list = ["ssh-key", action]
    if action == "add":
        argument_list.append(str(public_key_path))
    elif action == "rm":
        argument_list.append(SSH_FINGERPRINT)

    result = runner.invoke(cli, argument_list)

    assert result.exit_code == 2
    assert "Missing option '--provider'" in result.stderr
    runpod_http.assert_not_called()


@pytest.mark.parametrize("fingerprint", ["laptop", "SHA256:short", ""])
def test_invalid_fingerprint_fails_before_http(runner, runpod_http, fingerprint):
    """Require the complete identity instead of matching ambiguous comments or prefixes."""

    result = runner.invoke(cli, ["ssh-key", "rm", fingerprint, "--provider", "runpod", "-y"])

    assert result.exit_code == 2
    assert "full SHA256 fingerprint" in result.stderr
    runpod_http.assert_not_called()


@pytest.mark.parametrize(
    "response", [httpx.Response(403), httpx.Response(200, json={}), httpx.Response(200, json={"keys": ["broken"]})]
)
def test_failed_read_does_not_replace_keys(runner, runpod_http, public_key_path, response):
    """Never treat denied access or an invalid account response as an empty key list."""

    runpod_http.side_effect = [response]
    result = runner.invoke(cli, ["ssh-key", "add", str(public_key_path), "--provider", "runpod", "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error:" in result.stderr
    runpod_http.assert_called_once()
    (request,) = runpod_http.call_args.args
    assert request.method == "GET"


@pytest.mark.parametrize("action", ["add", "rm"])
def test_write_failure_has_no_success_output(runner, runpod_http, public_key_path, action):
    """Report a refused write and do not retry a whole-account key update."""

    argument_list = ["ssh-key", action]
    key_list = []
    if action == "add":
        argument_list.append(str(public_key_path))
    else:
        argument_list.extend([SSH_FINGERPRINT, "--yes"])
        key_list = [SSH_PUBLIC_KEY]
    runpod_http.side_effect = [httpx.Response(200, json={"keys": key_list}), httpx.Response(403)]

    result = runner.invoke(cli, [*argument_list, "--provider", "runpod", "--output", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "HTTP 403: PUT account/ssh-keys" in result.stderr
    assert runpod_http.call_count == 2
