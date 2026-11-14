from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class InstanceStatus(StrEnum):
    """Describe current instance state reported by the provider."""

    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DELETED = "deleted"


class Instance(BaseModel):
    """Return provider-neutral instance data for the GPU workflow."""

    id: str
    status: InstanceStatus
    region: str | None
    ssh_command: str | None
    gpu: str | None
    created_at: datetime


class Region(BaseModel):
    """Describe a provider region that can be selected for instance creation."""

    id: str
    name: str
    location: str


class GPUModel(BaseModel):
    """Describe a supported GPU and its catalog price in USD per GPU per hour."""

    id: str
    name: str
    hourly_price: float
    availability: Literal["LOW", "MEDIUM", "HIGH"]
    memory_gb: int
    min_ram_gb: int | None
    min_vcpu_count: int | None
