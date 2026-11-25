from collections.abc import Iterator

import httpx
from pydantic import BaseModel, JsonValue, SecretStr

from gripper.api_clients.base import APIClient
from gripper.api_clients.diagnostics import log_failure, log_request, log_response
from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse, MissingCredentials
from gripper.api_clients.runpod.schemas import (
    DataCenter,
    DataCenterCatalog,
    ErrorResponse,
    GPUCatalog,
    GPUType,
    Pod,
    PodPage,
    SSHKeyList,
)
from gripper.config import CONFIG_PATH


class RunpodAPIClient(APIClient):
    """Call Runpod REST API v2 without GPU or TTL business logic."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Configure authentication and a reusable HTTP connection pool."""

        if api_key is None or not api_key.get_secret_value().strip():
            raise MissingCredentials(f"Set api_key in [providers.runpod] in {CONFIG_PATH} before using Runpod.")

        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            event_hooks={"request": [log_request], "response": [log_response]},
        )

    def create_pod(self, payload: dict[str, JsonValue]) -> Pod:
        """Submit a Pod creation request once and parse the returned Pod."""

        response = self._request("POST", "pods", json=payload)
        return self._parse_response(response, Pod)

    def get_pod(self, pod_id: str) -> Pod:
        """Retrieve a Pod by its API identifier."""

        response = self._request("GET", f"pods/{pod_id}")
        return self._parse_response(response, Pod)

    def delete_pod(self, pod_id: str) -> None:
        """Terminate a Pod and require the documented deletion confirmation."""

        response = self._request("DELETE", f"pods/{pod_id}")
        if response.status_code != 204:
            raise InvalidAPIResponse("Runpod did not confirm Pod deletion with HTTP 204.")

    def get_pod_list(self) -> Iterator[Pod]:
        """Read standalone Pods across every cursor page."""

        params = {"limit": "100"}
        while True:
            response = self._request("GET", "pods", params=params)
            page = self._parse_response(response, PodPage)
            yield from page.pods

            if not page.pagination.hasNextPage:
                return
            if not page.pagination.nextCursor:
                raise InvalidAPIResponse("Runpod omitted the next Pod page cursor.")

            params["cursor"] = page.pagination.nextCursor

    def get_gpu_list(self) -> list[GPUType]:
        """Read GPU prices, hardware, and availability for one Secure Cloud Pod GPU."""

        response = self._request(
            "GET", "catalog/gpus", params={"include": "AVAILABILITY", "product": "POD", "cloud": "SECURE", "count": "1"}
        )
        return self._parse_response(response, GPUCatalog).gpus

    def get_data_center_list(self) -> list[DataCenter]:
        """Read valid data center identifiers from the catalog."""

        response = self._request("GET", "catalog/datacenters")
        return self._parse_response(response, DataCenterCatalog).dataCenters

    def get_ssh_key_list(self) -> list[str]:
        """Read all registered account public keys; this endpoint is not paginated."""

        response = self._request("GET", "account/ssh-keys")
        return self._parse_response(response, SSHKeyList).keys

    def replace_ssh_key_list(self, public_key_list: list[str]) -> list[str]:
        """Replace the account key set and parse Runpod's confirmed result."""

        response = self._request("PUT", "account/ssh-keys", json={"keys": public_key_list})
        return self._parse_response(response, SSHKeyList).keys

    def close(self) -> None:
        """Release the HTTP connection pool."""

        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, JsonValue] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Translate transport and HTTP failures without retrying requests."""

        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as exception:
            log_failure(exception)
            raise APIRequestFailed(f"Runpod request failed: {method} {path}.") from None

        if not response.is_success:
            raise APIRequestFailed(
                self._error_message(response, method, path),
                status_code=response.status_code,
            )

        return response

    def _error_message(self, response: httpx.Response, method: str, path: str) -> str:
        """Include documented error details and redact the API key if it is echoed back."""

        message = f"Runpod returned HTTP {response.status_code}: {method} {path}."
        try:
            problem = ErrorResponse.model_validate_json(response.content)
        except ValueError:
            return message

        detail = " ".join([problem.detail or problem.title, *problem.errors])
        api_key = self._client.headers["Authorization"].removeprefix("Bearer ")
        return f"{message} {detail.replace(api_key, '[REDACTED]')}"

    def _parse_response[ResponseModel: BaseModel](
        self, response: httpx.Response, model: type[ResponseModel]
    ) -> ResponseModel:
        """Parse endpoint data without including response payloads in errors."""

        try:
            return model.model_validate_json(response.content)
        except ValueError:
            raise InvalidAPIResponse("Runpod returned an invalid response.") from None
