import re
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel

from galloc.exceptions import InvalidSSHKey, SSHKeyGenerationFailed
from galloc.settings import Settings


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


def read_local_key_pair(public_key_path: Path) -> SSHKey:
    """Require a public key and its local private file before using them for GPU access."""

    if public_key_path.suffix != ".pub":
        raise InvalidSSHKey("Choose a .pub file with its private key beside it.")
    private_key_path = public_key_path.with_suffix("")
    if not private_key_path.is_file():
        raise InvalidSSHKey(f"Private key not found: {private_key_path}. Choose a key pair or generate one.")
    return read_public_key(public_key_path)


def configured_key_path(settings: Settings, provider_name: str) -> Path | None:
    """Resolve the provider's preferred public key, falling back to the shared default."""

    path = getattr(settings, f"{provider_name}_ssh_public_key") or settings.ssh_public_key
    return path.expanduser().absolute() if path is not None else None


def format_ssh_command(command: str | None, public_key_path: Path | None) -> str | None:
    """Use the chosen local identity without changing the provider's host, port, or other SSH options."""

    if command is None or public_key_path is None:
        return command
    executable, *argument_list = shlex.split(command)
    private_key_path = public_key_path.expanduser().with_suffix("")
    return shlex.join([executable, "-i", str(private_key_path), *argument_list])


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


def generate_key_pair(private_key_path: Path) -> Path:
    """Create an Ed25519 key pair without a passphrase, never replacing existing files."""

    public_key_path = private_key_path.with_name(f"{private_key_path.name}.pub")
    if private_key_path.exists() or public_key_path.exists():
        raise SSHKeyGenerationFailed(f"Key files already exist for {private_key_path}.")

    private_key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "galloc", "-f", str(private_key_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SSHKeyGenerationFailed(f"Could not generate {private_key_path}.")

    return public_key_path
