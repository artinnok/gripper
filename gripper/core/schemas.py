from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from gripper.core.ttl import as_utc


class GPU(BaseModel):
    """Map a local name to a provider instance and its deletion deadline."""

    model_config = ConfigDict(validate_assignment=True)

    id: UUID = Field(default_factory=uuid4)
    number: int | None = Field(default=None, gt=0)
    provider: str
    provider_instance_id: str | None = None
    expires_at: datetime

    @computed_field
    @property
    def name(self) -> str:
        """Expose the short local name once storage assigns a sequence number."""

        return f"gpu-{self.number}" if self.number is not None else str(self.id)

    @field_validator("expires_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Keep the deletion deadline timezone-aware and in UTC."""

        return as_utc(value)


class Creation(BaseModel):
    """Carry validated creation inputs in memory until the provider request finishes."""

    gpu: GPU
    gpu_model: str
    region: str
    duration: timedelta
    hourly_price: Decimal
