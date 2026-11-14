import json

import httpx
import pytest
from pydantic import SecretStr

from galloc.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse, MissingCredentials
from galloc.api_clients.runpod.client import RunpodAPIClient
from tests.conftest import make_gpu_data


def test_create_request_and_response(make_runpod_client, pod_data):
    """Send the supplied payload to the configured URL with bearer auth."""

    payload = {"name": "external-caller", "image": "example/image", "gpu": {"id": "NVIDIA H200", "count": 2}}

    def handler(request):
        """Assert the API protocol without applying provider defaults."""

        assert request.method == "POST"
        assert str(request.url) == "https://runpod.test/v2/pods"
        assert request.headers["Authorization"] == "Bearer test-api-key"
        assert request.headers["Accept"] == "application/json"
        assert request.extensions["timeout"]["read"] == 12
        assert json.loads(request.content) == payload
        return httpx.Response(201, json=pod_data)

    pod = make_runpod_client(handler).create_pod(payload)

    assert pod.id == pod_data["id"]
    assert pod.status == "RUNNING"
    assert pod.ssh.direct.command == "ssh root@195.26.233.3 -p 34446"


@pytest.mark.parametrize("status_code", [401, 403, 404, 409, 422, 429, 500])
def test_http_errors_preserve_status_without_retry(make_runpod_client, status_code):
    """Expose actionable HTTP status without leaking error bodies or retrying."""

    request_list = []

    def handler(request):
        """Return an error containing content that must stay out of messages."""

        request_list.append(request)
        return httpx.Response(status_code, text="test-api-key private-response")

    with pytest.raises(APIRequestFailed) as raised:
        make_runpod_client(handler).create_pod({})

    assert raised.value.status_code == status_code
    assert len(request_list) == 1
    assert "test-api-key" not in str(raised.value)
    assert "private-response" not in str(raised.value)


def test_gpu_catalog_requests_pod_availability(make_runpod_client):
    """Request Secure Cloud stock for one GPU and parse the returned hardware fields."""

    def handler(request):
        """Check the documented availability context rather than a default global catalog."""

        assert request.method == "GET"
        assert request.url.path == "/v2/catalog/gpus"
        assert dict(request.url.params) == {
            "include": "AVAILABILITY",
            "product": "POD",
            "cloud": "SECURE",
            "count": "1",
        }
        return httpx.Response(200, json={"gpus": [make_gpu_data()]})

    (gpu,) = make_runpod_client(handler).get_gpu_list()

    assert gpu.memory == 141
    assert gpu.minRam == 188
    assert gpu.minVcpuCount == 12
    assert gpu.availability == "HIGH"


def test_transport_failure_does_not_repeat_creation(make_runpod_client):
    """Report an uncertain creation outcome without repeating a billable request."""

    request_list = []

    def handler(request):
        """Simulate a timeout after the server could have created the Pod."""

        request_list.append(request)
        raise httpx.ReadTimeout("private transport detail", request=request)

    with pytest.raises(APIRequestFailed) as raised:
        make_runpod_client(handler).create_pod({})

    assert raised.value.status_code is None
    assert len(request_list) == 1
    assert "private transport detail" not in str(raised.value)


@pytest.mark.parametrize("body", ["not-json", "{}", '{"status": "UNKNOWN"}'])
def test_invalid_responses_fail_explicitly(make_runpod_client, body):
    """Do not turn malformed successful responses into missing resources."""

    def handler(request):
        """Return a response that violates the Pod schema."""

        return httpx.Response(200, text=body)

    with pytest.raises(InvalidAPIResponse):
        make_runpod_client(handler).get_pod("pod-1")


def test_unknown_pod_status_is_rejected(make_runpod_client, pod_data):
    """Fail on a new remote state instead of reporting a running instance."""

    pod_data["status"] = "UNKNOWN"

    def handler(request):
        """Return an otherwise valid Pod with an unrecognized status."""

        return httpx.Response(200, json=pod_data)

    with pytest.raises(InvalidAPIResponse):
        make_runpod_client(handler).get_pod("pod-1")


def test_pod_list_follows_cursor_pages(make_runpod_client, pod_data):
    """Read every page, including pages containing no Pods."""

    request_list = []

    def handler(request):
        """Serve an empty first page followed by the final matching Pod."""

        request_list.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v2/pods"
        assert request.url.params["limit"] == "100"
        if "cursor" not in request.url.params:
            return httpx.Response(
                200, json={"pods": [], "pagination": {"nextCursor": "next+page", "hasNextPage": True}}
            )

        assert request.url.params["cursor"] == "next+page"
        return httpx.Response(200, json={"pods": [pod_data], "pagination": {"nextCursor": None, "hasNextPage": False}})

    pod_list = list(make_runpod_client(handler).get_pod_list())

    assert [pod.id for pod in pod_list] == [pod_data["id"]]
    assert len(request_list) == 2


def test_missing_pagination_cursor_fails(make_runpod_client):
    """Reject incomplete pagination instead of silently losing resources."""

    def handler(request):
        """Return a non-final page without a usable cursor."""

        return httpx.Response(200, json={"pods": [], "pagination": {"nextCursor": None, "hasNextPage": True}})

    with pytest.raises(InvalidAPIResponse, match="cursor"):
        list(make_runpod_client(handler).get_pod_list())


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_credentials_fail_when_client_is_created(key):
    """Require credentials only when constructing the selected API client."""

    api_key = None
    if key is not None:
        api_key = SecretStr(key)

    with pytest.raises(MissingCredentials, match="GALLOC_RUNPOD_API_KEY"):
        RunpodAPIClient(api_key, base_url="https://runpod.test/v2/", timeout=30)


def test_context_manager_closes_connections(pod_data):
    """Release the HTTP client even when a calling operation fails."""

    def handler(request):
        """Return a valid Pod without accessing the network."""

        return httpx.Response(200, json=pod_data)

    with pytest.raises(ValueError, match="caller failed"):
        with RunpodAPIClient(
            SecretStr("test-api-key"),
            base_url="https://runpod.test/v2/",
            timeout=30,
            transport=httpx.MockTransport(handler),
        ) as client:
            raise ValueError("caller failed")

    with pytest.raises(RuntimeError, match="closed"):
        client.get_pod("pod-1")
