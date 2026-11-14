import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from galloc.exceptions import InvalidSSHKey


class SSHKey(BaseModel):
    """Describe a public key using its OpenSSH fingerprint and optional comment."""

    public_key: str
    fingerprint: str
    comment: str


def read_public_key(path: Path) -> SSHKey:
    """Read and validate one public key before contacting a provider."""

    try:
        value = path.read_text(encoding="utf-8")
    except UnicodeError:
        raise InvalidSSHKey("Use a UTF-8 OpenSSH public key file.") from None
    return parse_public_key(value)


def parse_public_key(value: str) -> SSHKey:
    """Validate a public key with OpenSSH and obtain its SHA256 fingerprint."""

    public_key = value.strip()
    match = re.fullmatch(r"(?:ssh|ecdsa|sk)-[^\s]+[ \t]+[^\s]+(?:[ \t]+(?P<comment>[^\r\n]*))?", public_key)
    if match is None:
        raise InvalidSSHKey("Expected one OpenSSH public key line, such as the contents of id_ed25519.pub.")

    result = subprocess.run(
        ["ssh-keygen", "-l", "-E", "sha256", "-f", "-"],
        input=public_key + "\n",
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise InvalidSSHKey("Invalid OpenSSH public key.")

    bits, fingerprint, description = result.stdout.split(maxsplit=2)
    return SSHKey(public_key=public_key, fingerprint=fingerprint, comment=match.group("comment") or "")
