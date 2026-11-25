import pytest

from gripper.main import cli
from tests.test_cli.conftest import TerminalInput


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize(
    "argument_list, message",
    [
        (["--region", "US-KS-2", "--ttl", "30m"], "Missing option '--gpu'"),
        (["--gpu", "H200", "--ttl", "30m"], "Missing option '--region'"),
        (["--gpu", "H200", "--region", "US-KS-2"], "Pass --ttl or --budget"),
        (
            ["--gpu", "H200", "--region", "US-KS-2", "--ttl", "30m", "--budget", "2.5"],
            "Supply exactly one of --ttl or --budget",
        ),
    ],
)
def test_add_requires_explicit_options(runner, runpod_http, database_path, interactive, argument_list, message):
    """Reject incomplete CLI input in terminals and pipes before any storage or API access."""

    result = runner.invoke(cli, ["add", *argument_list], input=TerminalInput() if interactive else None)

    assert result.exit_code == 2
    assert message in result.stderr
    assert result.stdout == ""
    assert not database_path.exists()
    runpod_http.assert_not_called()


def test_add_help_points_to_tui(runner, runpod_http):
    """Make the interactive entry point discoverable from command help."""

    result = runner.invoke(cli, ["add", "--help"])

    assert result.exit_code == 0
    assert "Run gripper without a command for interactive selection." in result.stdout
    runpod_http.assert_not_called()
