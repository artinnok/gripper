import json
from decimal import Decimal

import httpx
import pytest

from galloc.api_clients.exceptions import InvalidAPIResponse
from galloc.providers.exceptions import GPUUnavailable, InvalidGPU, InvalidRegion
from galloc.providers.schemas import InstanceStatus
from galloc.providers.vast.provider import VastProvider


def test_catalog_filters_disk_and_keeps_offer_identity(make_vast_client, vast_offer_data):
    """Expose distinct offers with truthful availability, compute prices, and hardware units."""

    def handler(request):
        """Require a bounded on-demand search with the actual configured disk size."""

        assert request.method == "POST"
        assert request.url.path == "/api/v0/bundles/"
        query = json.loads(request.content)
        assert query == {
            "type": "ondemand",
            "limit": 100,
            "order": [["dph_base", "asc"]],
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "num_gpus": {"eq": 1},
            "gpu_arch": {"eq": "nvidia"},
            "disk_space": {"gte": 64},
            "allocated_storage": 64,
            "geolocation": {"eq": "US"},
        }
        return httpx.Response(200, json={"offers": [vast_offer_data, vast_offer_data | {"id": 2345, "dph_base": 0.8}]})

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=64)
    expensive, cheap = provider.get_gpu_list(" us ")
    assert (expensive.id, cheap.id) == ("2345", "1234")
    assert cheap.name == "RTX 4090 #1234"
    assert (cheap.hourly_price, cheap.memory_gb, cheap.min_ram_gb, cheap.min_vcpu_count) == (0.3, 24, 64, 8)
    assert cheap.availability == "AVAILABLE"


def test_validation_and_region_lookup_pin_the_offer(make_vast_client, vast_offer_data):
    """Resolve the selected offer afresh and reject a different region without substituting hosts."""

    def handler(request):
        """Require an exact ID search even if other matching models exist elsewhere."""

        assert json.loads(request.content)["id"] == {"eq": 1234}
        return httpx.Response(200, json={"offers": [vast_offer_data]})

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    assert provider.validate_create("RTX 4090 #1234", "us") == ("1234", "US", Decimal("0.3"))
    (region,) = provider.get_region_list("1234")
    assert (region.id, region.location) == ("US", "Oregon, US")
    with pytest.raises(InvalidRegion):
        provider.validate_create("1234", "FR")
    with pytest.raises(InvalidGPU):
        provider.validate_create("RTX 4090", "US")


def test_sold_out_offer_requires_new_selection(make_vast_client):
    """Do not reuse stale stock or choose another machine when an offer disappears."""

    def handler(request):
        """Return a confirmed empty search for the selected ID."""

        return httpx.Response(200, json={"offers": []})

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(GPUUnavailable):
        provider.validate_create("1234", "US")


@pytest.mark.parametrize(
    "actual, intended, port, expected",
    [
        (None, "running", None, InstanceStatus.PROVISIONING),
        ("created", "running", 12345, InstanceStatus.PROVISIONING),
        ("loading", "running", 12345, InstanceStatus.PROVISIONING),
        ("exited", "running", 12345, InstanceStatus.PROVISIONING),
        ("running", "running", None, InstanceStatus.PROVISIONING),
        ("running", "running", 12345, InstanceStatus.RUNNING),
        ("exited", "stopped", 12345, InstanceStatus.STOPPED),
        ("offline", "running", 12345, InstanceStatus.ERROR),
    ],
)
def test_actual_status_and_ssh_determine_readiness(
    make_vast_client, vast_instance_data, actual, intended, port, expected
):
    """A requested running state alone must never make the container ready."""

    def handler(request):
        """Return actual and intended state independently."""

        data = vast_instance_data | {"actual_status": actual, "intended_status": intended, "ssh_port": port}
        return httpx.Response(200, json={"instances": data})

    instance = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50).get("5678")
    assert instance.status == expected
    assert instance.hourly_price == 0.34
    if expected == InstanceStatus.RUNNING:
        assert instance.ssh_command == "ssh root@ssh1.vast.ai -p 12345"
    else:
        assert instance.ssh_command is None


def test_unknown_status_is_not_running(make_vast_client, vast_instance_data):
    """Fail explicitly when the provider introduces an unrecognized container state."""

    def handler(request):
        """Return a new remote status."""

        return httpx.Response(200, json={"instances": vast_instance_data | {"actual_status": "new-state"}})

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(InvalidAPIResponse):
        provider.get("5678")
