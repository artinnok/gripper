from pathlib import Path

from pydantic import BaseModel, Field


SSH_USERNAME_PATTERN = r"[A-Za-z0-9_.][A-Za-z0-9_.-]*"
SSH_HOST_PATTERN = r"[A-Za-z0-9_.:\[][A-Za-z0-9_.:\[\]-]*"


class SSHConnection(BaseModel):
    """Describe the OpenSSH connection to one provider instance."""

    destination: str = Field(pattern=rf"^(?:{SSH_USERNAME_PATTERN}@)?{SSH_HOST_PATTERN}$")
    port: int | None = Field(default=None, ge=1, le=65535)
    identity: str | None = None
    tty_count: int = 0

    def build_argument_list(self, public_key_path: Path | None = None) -> list[str]:
        """Build the arguments used both in the displayed command and by the SSH process."""

        identity = self.identity
        if public_key_path is not None:
            identity = str(public_key_path.expanduser().with_suffix(""))

        argument_list = ["ssh"]
        if identity:
            argument_list.extend(["-i", identity])
        if self.tty_count:
            argument_list.append("-t" if self.tty_count == 1 else "-tt")
        argument_list.append(self.destination)
        if self.port is not None:
            argument_list.extend(["-p", str(self.port)])
        return argument_list
