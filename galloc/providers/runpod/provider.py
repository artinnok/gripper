from decimal import Decimal
from uuid import UUID

from galloc.api_clients.exceptions import APIRequestFailed
from galloc.api_clients.runpod.client import RunpodAPIClient
from galloc.api_clients.runpod.schemas import GPUType, Pod
from galloc.core.ssh_keys import SSHKey, parse_public_key
from galloc.providers.base import Provider
from galloc.providers.exceptions import CreationRejected, GPUUnavailable, InvalidGPU, InvalidRegion
from galloc.providers.runpod.ssh import get_ready_connection
from galloc.providers.schemas import GPUModel, Instance, InstanceStatus, Region


POD_STATUS_MAP = {
    "PROVISIONING": InstanceStatus.PROVISIONING,
    "STARTING": InstanceStatus.PROVISIONING,
    "RUNNING": InstanceStatus.RUNNING,
    "EXITED": InstanceStatus.STOPPED,
    "ERROR": InstanceStatus.ERROR,
    "TERMINATED": InstanceStatus.DELETED,
}


class RunpodProvider(Provider):
    """Manage one Secure Cloud Pod and its container disk per GPU."""

    def __init__(self, api_client: RunpodAPIClient, *, image: str, disk_gb: int) -> None:
        """Receive the API client and creation defaults from the caller."""

        self._api_client = api_client
        self._image = image
        self._disk_gb = disk_gb

    def check_credentials(self) -> None:
        """Verify the key against Runpod's authenticated data center catalog."""

        self._api_client.get_data_center_list()

    def validate_create(self, gpu: str, region: str) -> tuple[str, str, Decimal]:
        """Resolve available hardware and its catalog rate before the creation service starts."""

        gpu_type = self._resolve_gpu(gpu)
        data_center_id = self._resolve_region(region)
        region_id_list = self._available_region_id_list(gpu_type)
        if data_center_id not in region_id_list:
            raise GPUUnavailable(f"{gpu_type.name} is currently unavailable in {data_center_id} on Runpod.")

        hourly_price = Decimal(str(gpu_type.price.secure))
        if not hourly_price.is_finite() or hourly_price <= 0:
            raise InvalidGPU(f"Runpod returned no usable Secure Cloud price for {gpu_type.name}.")

        return gpu_type.id, data_center_id, hourly_price

    def create(self, gpu_id: UUID, gpu: str, region: str) -> Instance:
        """Create one Pod in the data center validated by the caller."""

        pod = self._create_pod(gpu_id, gpu, region)
        return self._to_instance(pod)

    def get(self, instance_id: str) -> Instance | None:
        """Read current state; a missing Pod means the resource is gone."""

        try:
            pod = self._api_client.get_pod(instance_id)
        except APIRequestFailed as exception:
            if exception.status_code == 404:
                return None
            raise

        if pod.status == "TERMINATED":
            return None
        return self._to_instance(pod)

    def delete(self, instance_id: str) -> None:
        """Terminate the Pod and its disk; already absent Pods need no work."""

        try:
            self._api_client.delete_pod(instance_id)
        except APIRequestFailed as exception:
            if exception.status_code != 404:
                raise

    def get_region_list(self, gpu: str | None = None) -> list[Region]:
        """List native data centers, optionally restricted to the selected GPU's capacity."""

        region_id_list = None
        if gpu is not None:
            region_id_list = self._available_region_id_list(self._resolve_gpu(gpu))

        return [
            Region(id=data_center.id, name=data_center.name, location=data_center.region.replace("_", " ").title())
            for data_center in self._api_client.get_data_center_list()
            if region_id_list is None or data_center.id in region_id_list
        ]

    def get_gpu_list(self, region: str | None = None) -> list[GPUModel]:
        """List available Secure Cloud GPUs by price, optionally limited to a region."""

        gpu_list = []
        for gpu in self._api_client.get_gpu_list():
            region_id_list = self._available_region_id_list(gpu)
            if not gpu.secure or not region_id_list:
                continue
            if region is not None and region.strip().upper() not in region_id_list:
                continue

            availability = gpu.availability
            if region is not None:
                (data_center,) = (center for center in gpu.dataCenters if center.id == region.strip().upper())
                availability = data_center.availability

            gpu_list.append(
                GPUModel(
                    id=gpu.id,
                    name=gpu.name,
                    hourly_price=gpu.price.secure,
                    availability=availability,
                    memory_gb=gpu.memory,
                    min_ram_gb=gpu.minRam,
                    min_vcpu_count=gpu.minVcpuCount,
                )
            )
        return sorted(gpu_list, key=lambda gpu: gpu.hourly_price, reverse=True)

    def get_ssh_key_list(self) -> list[SSHKey]:
        """Read account keys and identify them by fingerprint, independently of their comments."""

        return [parse_public_key(public_key) for public_key in self._api_client.get_ssh_key_list()]

    def create_ssh_key(self, ssh_key: SSHKey) -> bool:
        """Register one public key while preserving all existing account keys."""

        ssh_key_list = self.get_ssh_key_list()
        if any(key.fingerprint == ssh_key.fingerprint for key in ssh_key_list):
            return False

        public_key_list = [key.public_key for key in ssh_key_list]
        self._api_client.replace_ssh_key_list([*public_key_list, ssh_key.public_key])
        return True

    def delete_ssh_key(self, fingerprint: str) -> bool:
        """Remove only the matching key from Runpod's account-wide list."""

        ssh_key_list = self.get_ssh_key_list()
        public_key_list = [key.public_key for key in ssh_key_list if key.fingerprint != fingerprint]
        if len(public_key_list) == len(ssh_key_list):
            return False

        self._api_client.replace_ssh_key_list(public_key_list)
        return True

    def _resolve_gpu(self, gpu: str) -> GPUType:
        """Resolve a catalog display name or full identifier on Secure Cloud."""

        gpu_name = gpu.strip().casefold()
        gpu_list = [
            item
            for item in self._api_client.get_gpu_list()
            if item.secure and gpu_name in {item.name.casefold(), item.id.casefold()}
        ]
        if len(gpu_list) != 1:
            raise InvalidGPU(
                f"Choose one Runpod Secure Cloud GPU by its catalog name or ID: {gpu!r}. "
                "Run 'galloc' for interactive GPU selection."
            )

        (gpu_type,) = gpu_list
        if gpu_type.availability == "NONE":
            raise GPUUnavailable(f"{gpu_type.name} is currently unavailable on Runpod.")
        return gpu_type

    def _available_region_id_list(self, gpu: GPUType) -> list[str]:
        """Return only data centers with current Pod capacity for the selected GPU."""

        if gpu.availability == "NONE":
            return []
        return [data_center.id for data_center in gpu.dataCenters if data_center.availability != "NONE"]

    def _resolve_region(self, region: str) -> str:
        """Resolve a native data center ID without choosing another placement."""

        region_id = region.strip().casefold()
        for available_region in self.get_region_list():
            if available_region.id.casefold() == region_id:
                return available_region.id

        raise InvalidRegion(
            f"Unknown Runpod region {region!r}. Run 'galloc regions --provider runpod' to list regions."
        )

    def _create_pod(self, gpu_id: UUID, gpu_model_id: str, data_center_id: str) -> Pod:
        """Create one Pod, distinguishing explicit refusals from uncertain request failures."""

        try:
            return self._api_client.create_pod(
                {
                    "name": f"galloc-{gpu_id}",
                    "image": self._image,
                    "cloud": "SECURE",
                    "gpu": {"id": gpu_model_id, "count": 1},
                    "dataCenterIds": [data_center_id],
                    "disk": self._disk_gb,
                    "ports": ["22/tcp"],
                    "startSsh": True,
                }
            )
        except APIRequestFailed as exception:
            if exception.status_code in {400, 401, 402, 403, 404, 413, 422, 429}:
                raise CreationRejected(str(exception)) from exception
            raise

    def _to_instance(self, pod: Pod) -> Instance:
        """Convert the current Pod response into instance details."""

        status = POD_STATUS_MAP[pod.status]
        ssh = None
        if status == InstanceStatus.RUNNING:
            ssh = get_ready_connection(pod)
            if ssh is None:
                status = InstanceStatus.PROVISIONING

        return Instance(
            id=pod.id,
            status=status,
            region=pod.dataCenterId,
            ssh=ssh,
            gpu=pod.gpu.id if pod.gpu is not None else None,
            hourly_price=pod.cost,
            created_at=pod.createdAt,
        )
