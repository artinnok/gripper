import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from galloc.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse, MissingCredentials
from galloc.api_clients.vast.client import VastAPIClient


def test_create_uses_exact_offer_and_returns_contract_id(make_vast_client):
    """Keep the offer ID separate from the newly rented instance ID."""

    payload = {"image": "example/image", "disk": 64, "runtype": "ssh"}

    def handler(request):
        """Verify authentication and the documented creation protocol."""

        assert request.method == "PUT"
        assert str(request.url) == "https://vast.test/api/v0/asks/1234/"
        assert request.headers["Authorization"] == "Bearer vast-token"
        assert request.extensions["timeout"]["read"] == 12
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={"success": True, "new_contract": 5678})

    assert make_vast_client(handler).create_instance("1234", payload) == "5678"


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 410, 429, 500])
def test_refusals_are_descriptive_and_redacted(make_vast_client, caplog, status):
    """Treat success=false as a refusal even on HTTP 200 and never expose the credential."""

    def handler(request):
        """Return a documented failure containing an echoed credential."""

        return httpx.Response(status, json={"success": False, "msg": "Refused vast-token"})

    with caplog.at_level(logging.DEBUG, logger="galloc"), pytest.raises(APIRequestFailed) as raised:
        make_vast_client(handler).delete_instance("5678")
    assert raised.value.status_code == status
    assert "Refused [REDACTED]" in str(raised.value)
    assert "vast-token" not in caplog.text


@pytest.mark.parametrize("payload", [{}, {"success": True}, {"success": True, "new_contract": None}])
def test_creation_requires_a_contract_id(make_vast_client, payload):
    """Do not invent an instance ID after an incomplete success response."""

    def handler(request):
        """Omit the contract ID after an accepted creation request."""

        return httpx.Response(200, json=payload)

    with pytest.raises(InvalidAPIResponse):
        make_vast_client(handler).create_instance("1234", {})


@pytest.mark.parametrize("payload", [{}, {"instances": {}}, {"instances": []}])
def test_malformed_reads_do_not_confirm_absence(make_vast_client, payload):
    """Only an explicit null result can mean the instance is gone."""

    def handler(request):
        """Return a malformed successful instance response."""

        return httpx.Response(200, json=payload)

    with pytest.raises(InvalidAPIResponse):
        make_vast_client(handler).get_instance("5678")


def test_timeout_does_not_retry_creation(make_vast_client):
    """Leave an uncertain request unresolved instead of creating a duplicate rental."""

    request_list = []

    def handler(request):
        """Lose the response after the billable request may have reached Vast.ai."""

        request_list.append(request)
        raise httpx.ReadTimeout("private transport details", request=request)

    with pytest.raises(APIRequestFailed) as raised:
        make_vast_client(handler).create_instance("1234", {})
    assert len(request_list) == 1
    assert raised.value.status_code is None
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("token", [None, "", "   "])
def test_missing_credentials_are_local_and_provider_specific(token):
    """Direct the user to Vast.ai's TOML section before opening a connection."""

    with pytest.raises(MissingCredentials, match=r"providers.vast"):
        VastAPIClient(SecretStr(token) if token is not None else None, base_url="https://vast.test/", timeout=1)


def test_invalid_response_names_the_request_and_field_without_values(make_vast_client):
    """Say which answer could not be read and why, keeping every returned value out of the message."""

    def handler(request):
        """Return a key list whose entry lacks the key text."""

        return httpx.Response(200, json=[{"id": 22, "note": "returned-value"}])

    with pytest.raises(InvalidAPIResponse) as raised:
        make_vast_client(handler).get_ssh_key_list()
    assert str(raised.value) == "Vast.ai returned an invalid response to GET /api/v0/ssh/ (0.key: Field required)."
