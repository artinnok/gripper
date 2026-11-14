import re
from uuid import UUID

import click


def parse_gpu_reference(context: click.Context, parameter: click.Parameter, value: str) -> UUID | str:
    """Accept a short GPU name or an existing UUID before opening storage."""

    if re.fullmatch(r"gpu-[1-9][0-9]*", value):
        return value
    try:
        return UUID(value)
    except ValueError:
        raise click.BadParameter("Use a name such as gpu-1 or a GPU UUID.") from None


def parse_ssh_key_fingerprint(context: click.Context, parameter: click.Parameter, value: str) -> str:
    """Require a complete OpenSSH SHA256 fingerprint before a removal request."""

    if re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", value) is None:
        raise click.BadParameter("Use the full SHA256 fingerprint from 'galloc ssh-key ls --provider <provider>'.")
    return value
