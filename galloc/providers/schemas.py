from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


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
    ssh_command: str | None
    gpu: str | None
    hourly_price: float
    created_at: datetime | None


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
