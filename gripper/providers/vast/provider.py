from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError

from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse
from gripper.api_clients.vast.client import VastAPIClient
from gripper.api_clients.vast.schemas import Instance as VastInstance
from gripper.api_clients.vast.schemas import Offer
from gripper.core.ssh_keys import SSHKey, parse_public_key
from gripper.providers.base import Provider
from gripper.providers.exceptions import CreationRejected, GPUUnavailable, InvalidGPU, InvalidRegion
from gripper.providers.schemas import GPUModel, Instance, InstanceStatus, Region
from gripper.providers.ssh import SSHConnection


OFFER_LIMIT = 100
STATUS_MAP = {
    "created": InstanceStatus.PROVISIONING,
    "loading": InstanceStatus.PROVISIONING,
    "starting": InstanceStatus.PROVISIONING,
    "running": InstanceStatus.RUNNING,
    "exited": InstanceStatus.STOPPED,
    "stopped": InstanceStatus.STOPPED,
    "offline": InstanceStatus.ERROR,
    "error": InstanceStatus.ERROR,
}


class VastProvider(Provider):
    """Rent one verified, on-demand NVIDIA GPU offer with an instance-local disk."""

    def __init__(self, api_client: VastAPIClient, *, image: str, disk_gb: int) -> None:
        """Receive transport and defaults, keeping creation preparation only in memory."""

        self._api_client = api_client
        self._image = image
        self._disk_gb = disk_gb
        self._prepared_offer: Offer | None = None

    def check_credentials(self) -> None:
        """Verify account key access; Vast.ai's public catalog cannot validate credentials."""

        self._api_client.get_ssh_key_list()

    def validate_create(self, gpu: str, region: str) -> tuple[str, str, Decimal]:
        """Recheck the exact offer; never replace a sold-out selection with another host."""

        offer = self._resolve_offer(gpu)
        country = _country(offer.geolocation)
        if region.strip().upper() != country:
            raise InvalidRegion(f"Vast.ai offer {offer.id} is in {country}, not {region!r}.")

        self._prepared_offer = offer
        return str(offer.id), country, Decimal(str(offer.dph_base))

    def create(self, gpu_id: UUID, gpu: str, region: str) -> Instance:
        """Rent the prepared offer and return its ID immediately so TTL can track it."""

        try:
            instance_id = self._api_client.create_instance(
                gpu,
                {
                    "image": self._image,
                    "disk": self._disk_gb,
                    "label": f"gripper-{gpu_id}",
                    "runtype": "ssh",
                    "target_state": "running",
                    "cancel_unavail": True,
                },
            )
        except APIRequestFailed as exception:
            if exception.error_code == "no_such_ask":
                raise GPUUnavailable(f"Vast.ai offer {gpu} is no longer available. Choose another GPU.") from exception
            if exception.status_code in {200, 400, 401, 402, 403, 404, 410, 422, 429}:
                raise CreationRejected(str(exception)) from exception
            raise

        return Instance(
            id=instance_id,
            status=InstanceStatus.PROVISIONING,
            region=region,
            gpu=self._prepared_offer.gpu_name,
            hourly_price=self._prepared_offer.dph_base,
            created_at=datetime.now(UTC),
        )

    def get(self, instance_id: str) -> Instance | None:
        """Read actual container state; only confirmed absence discards a local mapping."""

        try:
            instance = self._api_client.get_instance(instance_id)
        except APIRequestFailed as exception:
            if exception.status_code == 404 and exception.error_code == "not_found":
                return None
            raise
        if instance is None:
            return None
        return _to_instance(instance)

    def delete(self, instance_id: str) -> None:
        """Destroy the instance and its disk; a confirmed absence is already complete."""

        try:
            self._api_client.delete_instance(instance_id)
        except APIRequestFailed as exception:
            if exception.status_code != 404 or exception.error_code != "not_found":
                raise

    def get_region_list(self, gpu: str | None = None) -> list[Region]:
        """Expose country codes as regions; a particular offer has exactly one location."""

        offer_list = [self._resolve_offer(gpu)] if gpu is not None else self._search_offer_list()
        region_map = {}
        for offer in offer_list:
            country = _country(offer.geolocation)
            region_map[country] = Region(id=country, name=country, location=offer.geolocation)
        return sorted(region_map.values(), key=lambda region: region.id)

    def get_gpu_list(self, region: str | None = None) -> list[GPUModel]:
        """List the cheapest offer of each GPU model and memory size."""

        gpu_map = {}
        for offer in sorted(self._search_model_offer_list(region), key=lambda offer: offer.dph_base):
            gpu = GPUModel(
                id=str(offer.id),
                name=offer.gpu_name,
                hourly_price=offer.dph_base,
                availability="AVAILABLE",
                # Vast.ai reports slightly less than the nominal size, such as 24564 MB for a 24 GB card.
                memory_gb=round(offer.gpu_ram / 1024),
                min_ram_gb=offer.cpu_ram // 1024 if offer.cpu_ram is not None else None,
                min_vcpu_count=int(offer.cpu_cores_effective) if offer.cpu_cores_effective is not None else None,
            )
            gpu_map.setdefault((gpu.name, gpu.memory_gb), gpu)

        return sorted(gpu_map.values(), key=lambda gpu: gpu.hourly_price, reverse=True)

    def get_ssh_key_list(self) -> list[SSHKey]:
        """Read account registrations as the same fingerprints used by the CLI and TUI."""

        return [parse_public_key(key.key) for key in self._api_client.get_ssh_key_list()]

    def create_ssh_key(self, ssh_key: SSHKey) -> bool:
        """Register a missing key without replacing other account registrations."""

        if any(key.fingerprint == ssh_key.fingerprint for key in self.get_ssh_key_list()):
            return False
        self._api_client.create_ssh_key(ssh_key.public_key)
        return True

    def delete_ssh_key(self, fingerprint: str) -> bool:
        """Remove only the registration matching the requested fingerprint."""

        for key in self._api_client.get_ssh_key_list():
            if parse_public_key(key.key).fingerprint == fingerprint:
                self._api_client.delete_ssh_key(key.id)
                return True
        return False

    def _resolve_offer(self, gpu: str) -> Offer:
        """Resolve one offer by its numeric ID; a GPU name alone matches many hosts."""

        offer_id = gpu.rsplit("#", 1)[-1].strip()
        if not offer_id.isdecimal():
            raise InvalidGPU("Choose a Vast.ai offer by its numeric ID. Run 'gripper' for interactive selection.")
        selected_id = int(offer_id)
        for offer in self._search_offer_list(offer_id=selected_id):
            if offer.id == selected_id:
                return offer
        raise GPUUnavailable(f"Vast.ai offer {offer_id} is no longer available. Choose another GPU.")

    def _search_model_offer_list(self, region: str | None) -> list[Offer]:
        """Repeat the search without the GPU names already found, until it brings no new model.

        One search returns only its cheapest offers, which are many hosts of a few inexpensive models.
        """

        offer_list = []
        name_set = set()
        while True:
            page_offer_list = self._search_offer_list(region=region, excluded_name_list=sorted(name_set))
            new_name_set = {offer.gpu_name for offer in page_offer_list} - name_set
            if not new_name_set:
                return offer_list

            offer_list += page_offer_list
            name_set |= new_name_set

    def _search_offer_list(
        self,
        *,
        region: str | None = None,
        offer_id: int | None = None,
        excluded_name_list: list[str] | None = None,
    ) -> list[Offer]:
        """Search rentable single-GPU offers with enough local disk for the configured image."""

        query = {
            "type": "ondemand",
            "limit": OFFER_LIMIT,
            "order": [["dph_base", "asc"]],
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "num_gpus": {"eq": 1},
            "gpu_arch": {"eq": "nvidia"},
            "disk_space": {"gte": self._disk_gb},
            "allocated_storage": self._disk_gb,
        }
        if region is not None:
            query["geolocation"] = {"eq": region.strip().upper()}
        if offer_id is not None:
            # The search matches the offer ID it returns only through this field; filtering by "id" finds nothing.
            query["ask_contract_id"] = {"eq": offer_id}
        if excluded_name_list:
            query["gpu_name"] = {"notin": excluded_name_list}
        return self._api_client.search_offer_list(query)


def _country(geolocation: str) -> str:
    """Use the country code at the end of Vast.ai's location as the placement identifier."""

    return geolocation.rsplit(",", 1)[-1].strip().upper()


def _to_instance(instance: VastInstance) -> Instance:
    """Expose a running container only after Vast.ai supplies its SSH endpoint."""

    status = InstanceStatus.PROVISIONING
    if instance.actual_status is not None:
        try:
            status = STATUS_MAP[instance.actual_status]
        except KeyError:
            raise InvalidAPIResponse(
                f"Vast.ai returned an unknown instance status: {instance.actual_status!r}."
            ) from None
    if status == InstanceStatus.STOPPED and instance.intended_status == "running":
        status = InstanceStatus.PROVISIONING

    ssh = None
    if status == InstanceStatus.RUNNING:
        ssh = _ssh_connection(instance)
        if ssh is None:
            status = InstanceStatus.PROVISIONING

    return Instance(
        id=str(instance.id),
        status=status,
        region=_country(instance.geolocation),
        ssh=ssh,
        gpu=instance.gpu_name,
        hourly_price=instance.dph_total,
        created_at=instance.start_date,
    )


def _ssh_connection(instance: VastInstance) -> SSHConnection | None:
    """Return an endpoint once Vast publishes a usable host and mapped port."""

    if not instance.ssh_host or not instance.ssh_port:
        return None
    try:
        return SSHConnection(destination=f"root@{instance.ssh_host}", port=instance.ssh_port)
    except ValidationError:
        return None
