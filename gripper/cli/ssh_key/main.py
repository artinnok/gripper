import click

from gripper.cli.ssh_key.add import add_command
from gripper.cli.ssh_key.ls import ls_command
from gripper.cli.ssh_key.rm import rm_command


@click.group("ssh-key")
def ssh_key_command() -> None:
    """Manage SSH public keys."""


ssh_key_command.add_command(add_command)
ssh_key_command.add_command(ls_command)
ssh_key_command.add_command(rm_command)
