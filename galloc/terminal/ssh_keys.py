import json

import click

from galloc.core.ssh_keys import SSHKey


def print_ssh_key_list(ssh_key_list: list[SSHKey], output: str) -> None:
    """Display current registrations as a two-column table or a JSON array."""

    if output == "json":
        click.echo(json.dumps([key.model_dump() for key in ssh_key_list], indent=2))
        return
    if not ssh_key_list:
        click.echo("No SSH keys registered.")
        return

    fingerprint_width = max(len("FINGERPRINT"), *(len(key.fingerprint) for key in ssh_key_list))
    click.echo(f"{'FINGERPRINT':<{fingerprint_width}}  COMMENT")
    for key in ssh_key_list:
        click.echo(f"{key.fingerprint:<{fingerprint_width}}  {key.comment}")


def print_ssh_key_creation(fingerprint: str, created: bool, output: str) -> None:
    """Report a new registration or an existing key without exposing local file contents."""

    if output == "json":
        click.echo(json.dumps({"fingerprint": fingerprint, "created": created}))
        return

    status = "Registered" if created else "Already registered"
    click.echo(f"{status}  {fingerprint}")


def print_ssh_key_removal(fingerprint: str, removed: bool, output: str) -> None:
    """Report removal or confirmed absence of an account key registration."""

    if output == "json":
        click.echo(json.dumps({"fingerprint": fingerprint, "removed": removed}))
        return

    status = "Removed" if removed else "Not registered"
    click.echo(f"{status}  {fingerprint}")
