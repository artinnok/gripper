import re
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID

from gripper.api_clients.exceptions import APIRequestFailed
from gripper.api_clients.lambda_.client import LambdaAPIClient
from gripper.api_clients.lambda_.schemas import CatalogEntry, InstanceType
from gripper.core.ssh_keys import SSHKey, parse_public_key, read_public_key
from gripper.exceptions import InvalidSSHKey
from gripper.providers.base import Provider
from gripper.providers.exceptions import CreationRejected, GPUUnavailable, InvalidGPU
from gripper.providers.schemas import GPUModel, Instance, InstanceStatus, Region
from gripper.providers.ssh import SSHConnection


INSTANCE_NOT_FOUND = "global/object-does-not-exist"
STATUS_MAP = {
    "booting": InstanceStatus.PROVISIONING,
    "active": InstanceStatus.RUNNING,
    "unhealthy": InstanceStatus.ERROR,
    "preempted": InstanceStatus.STOPPED,
    "terminating": InstanceStatus.TERMINATING,
    "terminated": InstanceStatus.DELETED,
}


class LambdaProvider(Provider):
    """Manage Lambda on-demand instances with their included local disk."""

    def __init__(
        self,
        api_client: LambdaAPIClient,
        *,
        disk_gb: int,
        public_key_path: Path | None,
        timeout: float,
        poll_interval: float,
    ) -> None:
        """Keep the validated type and SSH key name until the launch request."""

        self._api_client = api_client
        self._disk_gb = disk_gb
        self._public_key_path = public_key_path
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._instance_type: InstanceType | None = None
        self._key_name: str | None = None

    def check_credentials(self) -> None:
        """Verify account access through its SSH key collection."""

        self._api_client.get_ssh_key_list()

    def get_gpu_list(self, region: str | None = None) -> list[GPUModel]:
        """List rentable machine types by total hourly price, including the GPU count in the name."""

        gpu_list = []
        for entry in self._available_types():
            if region is not None and not any(place.name == region for place in entry.regions_with_capacity_available):
                continue

            machine = entry.instance_type
            memory = re.search(r"\b(\d+)\s*Gi?B\b", machine.gpu_description, re.IGNORECASE)
            gpu_list.append(
                GPUModel(
                    id=machine.name,
                    name=machine.description,
                    hourly_price=machine.price_cents_per_hour / 100,
                    availability="AVAILABLE",
                    memory_gb=int(memory.group(1)) if memory else None,
                    min_ram_gb=machine.specs.memory_gib,
                    min_vcpu_count=machine.specs.vcpus,
                )
            )
        return sorted(gpu_list, key=lambda gpu: gpu.hourly_price, reverse=True)

    def get_region_list(self, gpu: str | None = None) -> list[Region]:
        """List regions with current capacity for the requested type or any GPU."""

        entry_list = self._available_types() if gpu is None else [self._resolve_type(gpu)]
        region_map = {}
        for entry in entry_list:
            for place in entry.regions_with_capacity_available:
                region_map[place.name] = Region(id=place.name, name=place.name, location=place.description)

        return sorted(region_map.values(), key=lambda place: place.id)

    def validate_create(self, gpu: str, region: str) -> tuple[str, str, Decimal]:
        """Recheck placement and resolve the registered key before reserving a local GPU."""

        entry = self._resolve_type(gpu)
        if not any(place.name == region for place in entry.regions_with_capacity_available):
            raise GPUUnavailable(f"Lambda has no capacity for {gpu} in {region}.")

        self._key_name = self._resolve_key_name()
        self._instance_type = entry.instance_type
        return self._instance_type.name, region, Decimal(self._instance_type.price_cents_per_hour) / 100

    def create(self, gpu_id: UUID, gpu: str, region: str) -> Instance:
        """Launch once and return the provider ID before any readiness polling."""

        try:
            instance_id = self._api_client.create_instance(gpu, region, self._key_name, f"gripper-{gpu_id}")
        except APIRequestFailed as exception:
            if exception.error_code == "instance-operations/launch/insufficient-capacity":
                raise GPUUnavailable(f"Lambda has no capacity for {gpu} in {region}.") from exception
            if exception.status_code in {400, 401, 403, 404, 429}:
                raise CreationRejected(str(exception)) from exception
            raise

        return Instance(
            id=instance_id,
            status=InstanceStatus.PROVISIONING,
            region=region,
            gpu=self._instance_type.description,
            hourly_price=self._instance_type.price_cents_per_hour / 100,
            created_at=None,
        )

    def get(self, instance_id: str) -> Instance | None:
        """Return a usable Ubuntu SSH endpoint only when the provider reports an active machine."""

        try:
            instance = self._api_client.get_instance(instance_id)
        except APIRequestFailed as exception:
            if exception.status_code == 404 and exception.error_code == INSTANCE_NOT_FOUND:
                return None
            raise

        status = STATUS_MAP[instance.status]
        if status == InstanceStatus.DELETED:
            return None

        ssh = None
        if status == InstanceStatus.RUNNING and (instance.ip is None or instance.first_healthy is None):
            status = InstanceStatus.PROVISIONING

        if status == InstanceStatus.RUNNING:
            ssh = SSHConnection(destination=f"ubuntu@{instance.ip}")

        return Instance(
            id=instance.id,
            status=status,
            ssh=ssh,
            region=instance.region.name,
            gpu=instance.instance_type.description,
            hourly_price=instance.instance_type.price_cents_per_hour / 100,
            created_at=None,
        )

    def delete(self, instance_id: str) -> None:
        """Request termination once, then keep the local mapping until Lambda reports the instance gone."""

        instance = self.get(instance_id)
        if instance is None:
            return
        if instance.status != InstanceStatus.TERMINATING:
            self._api_client.terminate_instance(instance_id)

        deadline = monotonic() + self._timeout
        while (remaining := deadline - monotonic()) > 0:
            sleep(min(self._poll_interval, remaining))
            if self.get(instance_id) is None:
                return
        raise APIRequestFailed(f"Lambda has not yet confirmed termination of {instance_id}.")

    def get_ssh_key_list(self) -> list[SSHKey]:
        """Return the fingerprints shared with local key discovery and management."""

        return [parse_public_key(key.public_key) for key in self._api_client.get_ssh_key_list()]

    def create_ssh_key(self, ssh_key: SSHKey) -> bool:
        """Register a missing public key under a stable name independent of the OpenSSH comment."""

        if any(key.fingerprint == ssh_key.fingerprint for key in self.get_ssh_key_list()):
            return False

        digest = ssh_key.fingerprint.removeprefix("SHA256:").replace("/", "_").replace("+", "-")
        self._api_client.create_ssh_key(f"gripper-{digest}", ssh_key.public_key)
        return True

    def delete_ssh_key(self, fingerprint: str) -> bool:
        """Remove the matching Lambda registration by ID."""

        for key in self._api_client.get_ssh_key_list():
            if parse_public_key(key.public_key).fingerprint == fingerprint:
                self._api_client.delete_ssh_key(key.id)
                return True

        return False

    def _available_types(self) -> list[CatalogEntry]:
        """Limit the catalog to GPU instances with capacity and enough included disk."""

        return [
            entry
            for entry in self._api_client.get_catalog().values()
            if entry.regions_with_capacity_available
            and entry.instance_type.specs.gpus > 0
            and entry.instance_type.specs.storage_gib >= self._disk_gb
        ]

    def _resolve_type(self, gpu: str) -> CatalogEntry:
        """Resolve a native ID or an unambiguous display name in the current catalog."""

        entry_list = self._available_types()
        for entry in entry_list:
            if entry.instance_type.name == gpu:
                return entry

        match_list = [entry for entry in entry_list if entry.instance_type.description.casefold() == gpu.casefold()]
        if not match_list:
            raise GPUUnavailable(f"No Lambda capacity for {gpu!r} with at least {self._disk_gb} GiB of local disk.")
        if len(match_list) > 1:
            raise InvalidGPU(f"Multiple Lambda types match {gpu!r}. Use the instance type ID.")

        (instance_type_entry,) = match_list
        return instance_type_entry

    def _resolve_key_name(self) -> str:
        """Use the configured identity, or the sole account key when no preference was set."""

        key_list = self._api_client.get_ssh_key_list()
        if self._public_key_path is not None:
            key = read_public_key(self._public_key_path)
            for registered in key_list:
                if parse_public_key(registered.public_key).fingerprint == key.fingerprint:
                    return registered.name

        elif len(key_list) == 1:
            (registered,) = key_list
            return registered.name

        raise InvalidSSHKey(
            "Register your public key with 'gripper ssh-key add <path.pub> --provider lambda' "
            "and set ssh_public_key in [providers.lambda] to that path."
        )
