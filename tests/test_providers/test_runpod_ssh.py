import pytest

from gripper.providers.runpod.ssh import parse_ssh_command


@pytest.mark.parametrize(
    "command",
    ["other host", "ssh -F config host", "ssh host extra", "ssh 'unterminated", "ssh host -p 65536", "ssh 'bad host'"],
)
def test_unsupported_commands_have_no_ssh_endpoint(command):
    """Keep unexpected executables, options and remote commands out of SSH sessions."""

    assert parse_ssh_command(command) is None


def test_configured_identity_replaces_provider_hint(tmp_path):
    """Use the user's key with spaces in its path while retaining the provider's port and TTY."""

    key = tmp_path / "my keys" / "gpu.pub"
    connection = parse_ssh_command("ssh -p 2222 user@host -tt -i ~/.ssh/id_ed25519")

    assert connection.build_argument_list(key) == [
        "ssh",
        "-i",
        str(key.with_suffix("")),
        "-tt",
        "user@host",
        "-p",
        "2222",
    ]
    assert connection.build_argument_list() == ["ssh", "-i", "~/.ssh/id_ed25519", "-tt", "user@host", "-p", "2222"]
