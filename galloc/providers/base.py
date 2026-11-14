from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID

from galloc.providers.runpod.schemas import GPUModel, Instance, Region
from galloc.ssh_keys import SSHKey


class Provider(ABC):
    """Define provider operations independently of HTTP and local storage."""

    @abstractmethod
    def validate_create(self, gpu: str, region: str) -> tuple[str, str, Decimal]:
        """Return resolved GPU and region IDs and the catalog compute rate in USD/hour."""

    @abstractmethod
    def create(self, gpu_id: UUID, gpu: str, region: str) -> Instance:
        """Create from resolved inputs; raise CreationRejected only when no resources were created."""

    @abstractmethod
    def get(self, instance_id: str) -> Instance | None:
        """Read an instance, returning None only when it no longer exists."""

    @abstractmethod
    def delete(self, instance_id: str) -> None:
        """Confirm deletion of the instance and resources created with it."""

    @abstractmethod
    def get_region_list(self, gpu: str | None = None) -> list[Region]:
        """List native regions, limited to available capacity when a GPU is supplied."""

    @abstractmethod
    def get_gpu_list(self, region: str | None = None) -> list[GPUModel]:
        """List available GPUs by descending price, optionally limited to one region."""

    @abstractmethod
    def get_ssh_key_list(self) -> list[SSHKey]:
        """List public keys currently registered with the provider."""

    @abstractmethod
    def create_ssh_key(self, ssh_key: SSHKey) -> bool:
        """Register a validated public key; return False when it is already registered."""

    @abstractmethod
    def delete_ssh_key(self, fingerprint: str) -> bool:
        """Remove the selected registration; return False when it is already absent."""
