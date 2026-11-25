from datetime import datetime
from ipaddress import IPv4Address
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Response[T](BaseModel):
    """Unwrap the data returned by a successful Lambda request."""

    data: T


class Error(BaseModel):
    """Identify a provider refusal by its documented code and message."""

    code: str
    message: str


class Region(BaseModel):
    """Describe a Lambda region with available capacity."""

    name: str
    description: str


class Specs(BaseModel):
    """Read fixed resources included in an instance type."""

    vcpus: int
    memory_gib: int
    storage_gib: int
    gpus: int


class InstanceType(BaseModel):
    """Read the price of the whole machine in US cents per hour."""

    name: str
    description: str
    gpu_description: str
    price_cents_per_hour: int
    specs: Specs


class CatalogEntry(BaseModel):
    """Pair a machine type with its current regional capacity."""

    instance_type: InstanceType
    regions_with_capacity_available: list[Region]


class InstanceState(BaseModel):
    """Identify one machine and its provider-reported state."""

    id: str
    status: Literal["booting", "active", "unhealthy", "terminated", "terminating", "preempted"]


class Instance(InstanceState):
    """Read hardware, region and the public SSH address when assigned."""

    region: Region
    instance_type: InstanceType
    ip: IPv4Address | None = None
    first_healthy: datetime | None


class LaunchResult(BaseModel):
    """Require one provider ID for the single requested instance."""

    instance_ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, max_length=1)


class TerminationResult(BaseModel):
    """Read which instances Lambda accepted for termination."""

    terminated_instances: list[InstanceState]


class SSHKey(BaseModel):
    """Read the registered name needed at launch and ID needed for removal."""

    id: str
    name: str
    public_key: str
