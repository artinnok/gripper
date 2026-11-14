from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Read Runpod's problem details without including unrelated response fields."""

    title: str
    detail: str
    errors: list[str] = Field(default_factory=list)


class DataCenterAvailability(BaseModel):
    """Read stock for a GPU in one data center."""

    id: str
    availability: Literal["NONE", "LOW", "MEDIUM", "HIGH"]


class SSHEndpoint(BaseModel):
    """Read an SSH command supplied by Runpod."""

    command: str


class PodSSH(BaseModel):
    """Read the two connection methods in a Runpod response."""

    proxy: SSHEndpoint | None
    direct: SSHEndpoint | None


class PodGPUUtilization(BaseModel):
    """Read GPU telemetry reported by the running container."""

    util: float | None = None
    memoryUtil: float | None = None


class PodRuntime(BaseModel):
    """Read startup evidence from the live Pod runtime."""

    uptime: int | None = None
    gpus: list[PodGPUUtilization] = Field(default_factory=list)


class PodGPU(BaseModel):
    """Read the GPU identity returned for an actual Pod."""

    id: str


class Pod(BaseModel):
    """Parse the Pod fields used by the provider, retaining API field names."""

    id: str
    name: str
    status: Literal["PROVISIONING", "STARTING", "RUNNING", "EXITED", "ERROR", "TERMINATED"]
    dataCenterId: str | None
    ssh: PodSSH
    runtime: PodRuntime | None
    gpu: PodGPU | None
    createdAt: datetime


class Pagination(BaseModel):
    """Read Runpod's cursor pagination metadata."""

    nextCursor: str | None
    hasNextPage: bool


class PodPage(BaseModel):
    """Parse one page of standalone Pods."""

    pods: list[Pod]
    pagination: Pagination


class GPUPrice(BaseModel):
    """Read the Secure Cloud catalog price in USD per GPU per hour."""

    secure: float


class GPUType(BaseModel):
    """Read a GPU's deployment identifier, display name, cloud support, and price."""

    id: str
    name: str
    secure: bool
    price: GPUPrice
    memory: int
    availability: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    minRam: int | None = None
    minVcpuCount: int | None = None
    dataCenters: list[DataCenterAvailability] = Field(default_factory=list)


class GPUCatalog(BaseModel):
    """Parse the non-paginated GPU catalog."""

    gpus: list[GPUType]


class DataCenter(BaseModel):
    """Read a Runpod placement identifier, display name, and continental region."""

    id: str
    name: str
    region: str


class DataCenterCatalog(BaseModel):
    """Parse the non-paginated data center catalog."""

    dataCenters: list[DataCenter]


class SSHKeyList(BaseModel):
    """Read the complete account SSH key list returned by Runpod."""

    keys: list[str]
