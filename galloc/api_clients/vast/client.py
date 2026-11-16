import logging

import httpx
from pydantic import BaseModel, JsonValue, SecretStr

from galloc.api_clients.base import APIClient
from galloc.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse, MissingCredentials
from galloc.api_clients.vast.schemas import (
    CreateResponse,
    ErrorResponse,
    Instance,
    InstanceResponse,
    Offer,
    OfferList,
    SSHKey,
    SSHKeyList,
    SuccessResponse,
)


logger = logging.getLogger(__name__)


class VastAPIClient(APIClient):
    """Call Vast.ai REST endpoints without marketplace selection or TTL logic."""

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Configure bearer authentication and a reusable HTTP connection pool."""

        if api_key is None or not api_key.get_secret_value().strip():
            raise MissingCredentials("Set api_key in [providers.vast] in ~/.galloc/config.toml before using Vast.ai.")

        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def search_offer_list(self, query: dict[str, JsonValue]) -> list[Offer]:
        """Return the explicitly bounded, filtered marketplace search."""

        response = self._request("POST", "bundles/", json=query)
        return self._parse_response(response, OfferList).offers

    def create_instance(self, offer_id: str, payload: dict[str, JsonValue]) -> str:
        """Accept a particular offer once and return its new contract ID."""

        response = self._request("PUT", f"asks/{offer_id}/", json=payload)
        return str(self._parse_response(response, CreateResponse).new_contract)

    def get_instance(self, instance_id: str) -> Instance | None:
        """Read current container details; a null instance confirms absence."""

        response = self._request("GET", f"instances/{instance_id}/")
        return self._parse_response(response, InstanceResponse).instances

    def delete_instance(self, instance_id: str) -> None:
        """Destroy the instance and its local disk, requiring explicit success."""

        response = self._request("DELETE", f"instances/{instance_id}/")
        self._parse_response(response, SuccessResponse)

    def get_ssh_key_list(self) -> list[SSHKey]:
        """Read account public keys; the endpoint returns a complete array."""

        response = self._request("GET", "ssh/")
        return self._parse_response(response, SSHKeyList).root

    def create_ssh_key(self, public_key: str) -> None:
        """Register one account key without replacing existing registrations."""

        response = self._request("POST", "ssh/", json={"ssh_key": public_key})
        self._parse_response(response, SuccessResponse)

    def delete_ssh_key(self, key_id: int) -> None:
        """Remove one key by its provider ID and require confirmation."""

        response = self._request("DELETE", f"ssh/{key_id}/")
        self._parse_response(response, SuccessResponse)

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
        """Translate transport, HTTP, and explicit API refusals without retrying requests."""

        logger.debug("Vast.ai request: %s %s", method, path)
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.RequestError:
            raise APIRequestFailed(f"Vast.ai request failed: {method} {path}.") from None

        logger.debug("Vast.ai response: %s %s HTTP %s", method, path, response.status_code)
        try:
            problem = ErrorResponse.model_validate_json(response.content)
        except ValueError:
            problem = ErrorResponse()
        if not response.is_success or problem.success is False:
            detail = problem.msg or problem.detail or problem.error
            api_key = self._client.headers["Authorization"].removeprefix("Bearer ")
            detail = detail.replace(api_key, "[REDACTED]")
            raise APIRequestFailed(
                f"Vast.ai rejected {method} {path} (HTTP {response.status_code}). {detail}".strip(),
                status_code=response.status_code,
                error_code=problem.error or None,
            )
        return response

    def _parse_response[ResponseModel: BaseModel](
        self, response: httpx.Response, model: type[ResponseModel]
    ) -> ResponseModel:
        """Validate endpoint data without including sensitive response payloads in errors."""

        try:
            return model.model_validate_json(response.content)
        except ValueError:
            raise InvalidAPIResponse("Vast.ai returned an invalid response.") from None
