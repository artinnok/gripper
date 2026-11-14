import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from galloc.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse
from galloc.providers.exceptions import CreationRejected, InvalidGPU, InvalidRegion
from galloc.providers.runpod.provider import RunpodProvider
from galloc.providers.runpod.schemas import InstanceStatus
from tests.conftest import make_gpu_data


@pytest.mark.parametrize("gpu", ["h200", "NVIDIA H200"])
def test_create_maps_catalog_and_tracks_owned_resource(make_runpod_client, pod_data, gpu):
    """Create one named Pod in the requested location without separate volumes."""

    gpu_id = uuid4()
    pod_data.update(name=f"galloc-{gpu_id}", status="PROVISIONING", dataCenterId=None)
    request_list = []

    def handler(request):
        """Serve catalog reads and capture the only creation request."""

        request_list.append(request)
        if request.url.path == "/v2/catalog/gpus":
            return httpx.Response(
                200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
            )
        if request.url.path == "/v2/catalog/datacenters":
            return httpx.Response(
                200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
            )
        assert request.method == "POST"
        assert request.url.path == "/v2/pods"
        return httpx.Response(201, json=pod_data)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image:1", disk_gb=80)
    gpu_model_id, region, hourly_price = provider.validate_create(gpu, " us-ks-2 ")
    assert len(request_list) == 2
    assert hourly_price == Decimal("5.29")
    instance = provider.create(gpu_id, gpu_model_id, region)

    _, _, creation_request = request_list
    assert json.loads(creation_request.content) == {
        "name": f"galloc-{gpu_id}",
        "image": "example/image:1",
        "cloud": "SECURE",
        "gpu": {"id": "NVIDIA H200", "count": 1},
        "dataCenterIds": ["US-KS-2"],
        "disk": 80,
        "ports": ["22/tcp"],
        "startSsh": True,
    }
    assert len(request_list) == 3
    assert instance.status == InstanceStatus.PROVISIONING
    assert instance.region is None
    assert instance.ssh_command is None
    assert instance.gpu == pod_data["gpu"]["id"]
    assert instance.created_at.isoformat() == "2026-06-01T12:00:00+00:00"


@pytest.mark.parametrize(
    "gpu_list",
    [
        [],
        [make_gpu_data(id="NVIDIA H200", name="H200", secure=False, price={"secure": 0})],
        [
            make_gpu_data(id="H200-1", name="H200", secure=True, price={"secure": 5.29}),
            make_gpu_data(id="H200-2", name="H200", secure=True, price={"secure": 5.29}),
        ],
    ],
)
def test_unresolved_gpu_does_not_create_a_pod(make_runpod_client, gpu_list):
    """Reject missing, unsupported, or ambiguous GPU choices before creation."""

    def handler(request):
        """Allow only the GPU catalog read."""

        assert request.method == "GET"
        assert request.url.path == "/v2/catalog/gpus"
        return httpx.Response(200, json={"gpus": gpu_list})

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(InvalidGPU):
        provider.validate_create("H200", "US-KS-2")


def test_invalid_region_does_not_create_a_pod(make_runpod_client):
    """Reject unknown regions instead of creating outside the requested geography."""

    def handler(request):
        """Serve only the two read-only catalog requests."""

        assert request.method == "GET"
        if request.url.path == "/v2/catalog/gpus":
            return httpx.Response(
                200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
            )
        assert request.url.path == "/v2/catalog/datacenters"
        return httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        )

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(InvalidRegion):
        provider.validate_create("H200", "missing")


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404, 413, 422, 429, 408, 500])
def test_creation_distinguishes_rejection_from_uncertain_failure(make_runpod_client, status_code):
    """Confirm known refusals without treating timeouts or server errors as proof of absence."""

    def handler(request):
        """Reject or interrupt creation without permitting repeated catalog validation."""

        assert request.method == "POST"
        return httpx.Response(status_code)

    expected_exception = CreationRejected
    if status_code in {408, 500}:
        expected_exception = APIRequestFailed

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(expected_exception):
        provider.create(uuid4(), "NVIDIA H200", "US-KS-2")


def test_failed_catalog_cannot_leave_a_created_resource(make_runpod_client):
    """Report catalog failures during preparation before any resource creation."""

    def handler(request):
        """Fail the first read before any mutation is possible."""

        assert request.method == "GET"
        return httpx.Response(500)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(APIRequestFailed):
        provider.validate_create("H200", "US-KS-2")


@pytest.mark.parametrize(
    ("remote_status", "expected_status"),
    [
        ("PROVISIONING", InstanceStatus.PROVISIONING),
        ("STARTING", InstanceStatus.PROVISIONING),
        ("RUNNING", InstanceStatus.RUNNING),
        ("EXITED", InstanceStatus.STOPPED),
        ("ERROR", InstanceStatus.ERROR),
        ("TERMINATED", None),
    ],
)
def test_get_maps_actual_status(make_runpod_client, pod_data, remote_status, expected_status):
    """Distinguish readiness, stopped resources, errors, and completed deletion."""

    pod_data["status"] = remote_status

    def handler(request):
        """Return the requested Pod in its current remote state."""

        assert request.method == "GET"
        assert request.url.path == f"/v2/pods/{pod_data['id']}"
        return httpx.Response(200, json=pod_data)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    instance = provider.get(pod_data["id"])

    if expected_status is None:
        assert instance is None
        return
    assert instance.status == expected_status
    assert instance.region == "US-KS-2"
    if remote_status == "RUNNING":
        assert instance.ssh_command == "ssh root@195.26.233.3 -p 34446"
    else:
        assert instance.ssh_command is None


def test_get_uses_proxy_when_direct_ssh_is_unavailable(make_runpod_client, pod_data):
    """Expose the supplied proxy command when no direct mapping is available."""

    pod_data["ssh"]["direct"] = None

    def handler(request):
        """Return a running Pod without a published public port."""

        return httpx.Response(200, json=pod_data)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    assert provider.get(pod_data["id"]).ssh_command == "ssh 7h9k2m4n6p-64411eb2@ssh.runpod.io"


def test_get_missing_pod_returns_none(make_runpod_client):
    """Translate a missing resource into the provider's absence result."""

    def handler(request):
        """Report an instance that was removed outside galloc."""

        return httpx.Response(404)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    assert provider.get("missing") is None


def test_repeated_delete_accepts_confirmed_absence(make_runpod_client):
    """Delete the Pod and its disk once, then accept an already absent Pod."""

    status_list = iter([204, 404])

    def handler(request):
        """Respond only to deletion of the requested Pod."""

        assert request.method == "DELETE"
        assert request.url.path == "/v2/pods/pod-1"
        return httpx.Response(next(status_list))

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    provider.delete("pod-1")
    provider.delete("pod-1")


@pytest.mark.parametrize("operation", ["get", "delete"])
@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_provider_does_not_treat_api_errors_as_absence(make_runpod_client, operation, status_code):
    """Preserve unfinished work when authentication, rate limits, or servers fail."""

    def handler(request):
        """Return a failure that does not confirm resource deletion."""

        return httpx.Response(status_code)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(APIRequestFailed):
        getattr(provider, operation)("pod-1")


@pytest.mark.parametrize("status_code", [200, 202])
def test_delete_requires_confirmation(make_runpod_client, status_code):
    """Do not report deletion complete on an undocumented success response."""

    def handler(request):
        """Return acceptance without the documented completed-deletion status."""

        return httpx.Response(status_code)

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(InvalidAPIResponse):
        provider.delete("pod-1")
