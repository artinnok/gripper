from textual.markup import escape

from galloc.core.ssh_keys import SSHKey


def format_ssh_key(ssh_key: SSHKey) -> str:
    """Show a selected public key's name, fingerprint, and contents in the detail column."""

    return (
        f"[b $primary]{escape(ssh_key.comment or 'SSH key')}[/]\n\n"
        f"[$muted]fingerprint[/]\n{escape(ssh_key.fingerprint)}\n\n"
        f"[$muted]public key[/]\n{escape(ssh_key.public_key)}"
    )
