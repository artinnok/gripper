from time import sleep

import httpx
from pydantic import JsonValue, SecretStr, ValidationError

from gripper.api_clients.base import APIClient
from gripper.api_clients.diagnostics import log_failure, log_request, log_response
from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse, MissingCredentials
from gripper.api_clients.lambda_.schemas import (
    CatalogEntry,
    Error,
    Instance,
    LaunchResult,
    Response,
    SSHKey,
    TerminationResult,
)
from gripper.config import CONFIG_PATH


class LambdaAPIClient(APIClient):
    """Call the Lambda on-demand Cloud API with a user-supplied bearer token."""

    def __init__(
        self, api_key: SecretStr | None, *, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        """Configure authentication and a shared HTTP connection pool."""

        if api_key is None or not api_key.get_secret_value().strip():
            raise MissingCredentials(f"Set api_key in [providers.lambda] in {CONFIG_PATH} before using Lambda.")

        self._timeout = timeout
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=timeout,
            transport=transport,
            event_hooks={"request": [log_request], "response": [log_response]},
        )

    def get_catalog(self) -> dict[str, CatalogEntry]:
        """Read the current instance types and available regions."""

        return self._data(self._request("GET", "instance-types"), dict[str, CatalogEntry])

    def create_instance(self, gpu: str, region: str, key_name: str, name: str) -> str:
        """Launch exactly one machine with the selected account SSH key."""

        payload = {"instance_type_name": gpu, "region_name": region, "ssh_key_names": [key_name], "name": name}
        response = self._request("POST", "instance-operations/launch", json=payload)
        (instance_id,) = self._data(response, LaunchResult).instance_ids
        return instance_id

    def get_instance(self, instance_id: str) -> Instance:
        """Read one machine's status and connection details."""

        return self._data(self._request("GET", f"instances/{instance_id}"), Instance)

    def terminate_instance(self, instance_id: str) -> None:
        """Require a termination receipt for the requested machine."""

        response = self._request("POST", "instance-operations/terminate", json={"instance_ids": [instance_id]})
        terminated_list = self._data(response, TerminationResult).terminated_instances
        if not any(instance.id == instance_id for instance in terminated_list):
            raise InvalidAPIResponse(f"Lambda did not confirm termination of instance {instance_id}.")

    def get_ssh_key_list(self) -> list[SSHKey]:
        """List public key registrations in the account."""

        return self._data(self._request("GET", "ssh-keys"), list[SSHKey])

    def create_ssh_key(self, name: str, public_key: str) -> None:
        """Upload only the public half of the chosen key pair."""

        response = self._request("POST", "ssh-keys", json={"name": name, "public_key": public_key})
        self._data(response, SSHKey)

    def delete_ssh_key(self, key_id: str) -> None:
        """Delete a public key registration by its Lambda ID."""

        self._data(self._request("DELETE", f"ssh-keys/{key_id}"), dict)

    def close(self) -> None:
        """Release HTTP connections."""

        self._client.close()

    def _request(
        self, method: str, path: str, *, json: dict[str, JsonValue] | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """Honor Lambda's Retry-After once, only when the API explicitly refused the request."""

        for attempt in range(2):
            try:
                response = self._client.request(method, path, json=json, params=params)
            except httpx.RequestError as exception:
                log_failure(exception)
                raise APIRequestFailed(f"Lambda request failed: {method} {path}.") from None

            if response.is_success:
                return response
            error = self._error(response)
            delay = response.headers.get("Retry-After", "")
            if attempt or response.status_code != 429 or error.error_code != "global/rate-limited":
                raise error
            if not delay.isascii() or not delay.isdecimal() or float(delay) > self._timeout:
                raise error
            sleep(float(delay))

    def _error(self, response: httpx.Response) -> APIRequestFailed:
        """Report the failed endpoint and provider reason without echoing its credential."""

        request = response.request
        message = f"Lambda rejected {request.method} {request.url.path} (HTTP {response.status_code})."
        try:
            error = Error.model_validate(response.json()["error"])
        except ValueError, KeyError, TypeError:
            return APIRequestFailed(message, response.status_code)

        api_key = self._client.headers["Authorization"].removeprefix("Bearer ")
        detail = error.message.replace(api_key, "[REDACTED]")
        return APIRequestFailed(f"{message} {detail}", response.status_code, error_code=error.code)

    def _data[T](self, response: httpx.Response, model: type[T]) -> T:
        """Validate the endpoint envelope without exposing response values."""

        try:
            return Response[model].model_validate_json(response.content).data
        except ValidationError as exception:
            field = ".".join(str(part) for part in exception.errors(include_input=False)[0]["loc"]) or "body"
            raise InvalidAPIResponse(
                f"Lambda returned an invalid response to {response.request.url.path} ({field})."
            ) from None
