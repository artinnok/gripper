import shlex
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from galloc.providers.ssh import SSHConnection


class ProviderName(StrEnum):
    """Identify the supported GPU providers."""

    RUNPOD = "runpod"
    VAST = "vast"
    LAMBDA = "lambda"


class InstanceStatus(StrEnum):
    """Describe current instance state reported by the provider."""

    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    TERMINATING = "terminating"
    ERROR = "error"
    DELETED = "deleted"


class Instance(BaseModel):
    """Return provider-neutral instance data for the GPU workflow."""

    id: str
    status: InstanceStatus
    region: str | None
    ssh: SSHConnection | None = Field(default=None, exclude=True)
    gpu: str | None
    hourly_price: float
    created_at: datetime | None

    def format_ssh_command(self, public_key_path: Path | None = None) -> str | None:
        """Quote the connection arguments for a command that can be pasted into a shell."""

        if self.ssh is None:
            return None
        return shlex.join(self.ssh.build_argument_list(public_key_path))

    @computed_field
    @property
    def ssh_command(self) -> str | None:
        """Include the provider's SSH command in CLI JSON output."""

        return self.format_ssh_command()


class Region(BaseModel):
    """Describe a provider region that can be selected for instance creation."""

    id: str
    name: str
    location: str


class GPUModel(BaseModel):
    """Describe a supported GPU and its catalog price in USD per instance per hour."""

    id: str
    name: str
    hourly_price: float
    availability: Literal["LOW", "MEDIUM", "HIGH", "AVAILABLE"]
    memory_gb: int | None
    min_ram_gb: int | None
    min_vcpu_count: int | None
