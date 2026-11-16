from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PositiveInt, RootModel


class Offer(BaseModel):
    """Read one rentable machine offer, with memory in MB and compute pricing in USD/hour."""

    id: PositiveInt
    gpu_name: str
    gpu_ram: int
    geolocation: str
    dph_base: float = Field(gt=0, allow_inf_nan=False)
    cpu_ram: int | None = None
    cpu_cores_effective: float | None = None


class OfferList(BaseModel):
    """Read the bounded result of a marketplace search."""

    offers: list[Offer]


class Instance(BaseModel):
    """Read actual container state independently of the requested target state."""

    id: PositiveInt
    actual_status: str | None
    intended_status: str
    geolocation: str
    gpu_name: str
    dph_total: float = Field(ge=0, allow_inf_nan=False)
    start_date: datetime
    ssh_host: str | None = None
    ssh_port: int | None = Field(default=None, ge=0, le=65535)


class InstanceResponse(BaseModel):
    """Read one instance; a null result confirms that it no longer exists."""

    instances: Instance | None


class SuccessResponse(BaseModel):
    """Require explicit acknowledgement before treating a mutation as complete."""

    success: Literal[True]


class CreateResponse(SuccessResponse):
    """Read the new contract ID before making any further provider requests."""

    new_contract: PositiveInt


class SSHKey(BaseModel):
    """Read a registered key and its provider ID for individual removal."""

    id: PositiveInt
    key: str


class SSHKeyList(RootModel[list[SSHKey]]):
    """Read the unpaginated account key array."""


class ErrorResponse(BaseModel):
    """Read documented failure fields without exposing unrelated response data."""

    success: bool | None = None
    msg: str = ""
    error: str = ""
    detail: str = ""
