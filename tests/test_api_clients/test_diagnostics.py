import json
import logging
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from galloc.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse


@pytest.mark.parametrize("fixture_name", ["make_runpod_client", "make_vast_client"])
def test_http_diagnostics_include_payloads_and_mask_secrets(request, fixture_name, caplog):
    """Log real request and error bodies for both providers without altering or exposing credentials."""

    make_client = request.getfixturevalue(fixture_name)
    payload = {"gpu": {"id": "RTX 4090"}, "nested": [{"access_token": "body-secret"}], "env": {"PWD": "env-secret"}}

    def handler(request):
        """Confirm that diagnostic redaction never changes the actual transmitted data."""

        assert json.loads(request.content) == payload
        assert request.url.params["api_key"] == "query-secret"
        token = request.headers["authorization"].removeprefix("Bearer ")
        return httpx.Response(
            400,
            headers={"set-cookie": "session=cookie-secret", "x-request-id": "provider-request-123"},
            json={
                "detail": f"Offer unavailable for {token}",
                "code": "no_stock",
                "nested": {"password": "response-secret"},
            },
        )

    with caplog.at_level(logging.DEBUG, logger="galloc"), pytest.raises(APIRequestFailed):
        make_client(handler)._request(
            "POST", "offers", json=payload, params={"limit": "100", "api_key": "query-secret"}
        )

    assert "REQUEST POST https://" in caplog.text
    assert "limit=100" in caplog.text
    assert '"id": "RTX 4090"' in caplog.text
    assert "HTTP 400" in caplog.text
    assert '"code": "no_stock"' in caplog.text
    assert "provider-request-123" in caplog.text
    assert "Offer unavailable for [REDACTED]" in caplog.text
    assert re.search(r"\(\d+\.\d{3}s\)", caplog.text)
    for secret in (
        "test-api-key",
        "vast-token",
        "body-secret",
        "env-secret",
        "query-secret",
        "response-secret",
        "cookie-secret",
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize("fixture_name", ["make_runpod_client", "make_vast_client"])
def test_transport_failure_is_paired_with_the_request(request, fixture_name, caplog):
    """Record the transport exception and duration even when no response arrives."""

    def handler(request):
        """Fail after sending the request, including an echoed token in the error."""

        token = request.headers["authorization"].removeprefix("Bearer ")
        raise httpx.ReadTimeout(f"Timed out with {token}", request=request)

    with caplog.at_level(logging.DEBUG, logger="galloc"), pytest.raises(APIRequestFailed):
        request.getfixturevalue(fixture_name)(handler)._request("POST", "offers", json={"disk": 50})

    request_id, failure_id = re.findall(r"\[([a-f0-9]{8})\] (?:REQUEST|FAILED)", caplog.text)
    assert request_id == failure_id
    assert "ReadTimeout" in caplog.text
    assert "Timed out with [REDACTED]" in caplog.text
    assert "test-api-key" not in caplog.text and "vast-token" not in caplog.text


@pytest.mark.parametrize("body", ['{"unexpected": "schema-debug-value"}', "<html>gateway failure</html>\x1b[2J"])
def test_invalid_responses_are_logged_before_schema_validation(make_vast_client, caplog, body):
    """Keep malformed JSON and non-JSON bodies visible while escaping terminal control codes."""

    def handler(request):
        """Return a successful response with unusable provider data."""

        return httpx.Response(200, text=body)

    with caplog.at_level(logging.DEBUG, logger="galloc"), pytest.raises(InvalidAPIResponse):
        make_vast_client(handler).get_instance("5678")
    assert "HTTP 200" in caplog.text
    assert "schema-debug-value" in caplog.text or "gateway failure" in caplog.text
    assert "\x1b" not in caplog.text


def test_parallel_requests_have_distinct_matching_ids(make_vast_client, caplog):
    """Keep concurrent provider responses distinguishable when their log records interleave."""

    barrier = Barrier(2)

    def handler(request):
        """Hold both requests until they are in flight together."""

        barrier.wait(timeout=5)
        return httpx.Response(200, json={"instances": None})

    client = make_vast_client(handler)
    with caplog.at_level(logging.DEBUG, logger="galloc"), ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(client.get_instance, ["123", "456"])) == [None, None]

    request_id_list = re.findall(r"\[([a-f0-9]{8})\] REQUEST", caplog.text)
    response_id_list = re.findall(r"\[([a-f0-9]{8})\] RESPONSE", caplog.text)
    assert len(set(request_id_list)) == 2
    assert sorted(request_id_list) == sorted(response_id_list)


def test_debug_off_emits_no_request_data(make_vast_client, caplog):
    """Keep bodies and diagnostics out of normal provider operations."""

    def handler(request):
        """Return provider data that should not be logged at the normal level."""

        return httpx.Response(200, json={"instances": None, "extra": "debug-only-body"})

    with caplog.at_level(logging.WARNING, logger="galloc"):
        assert make_vast_client(handler).get_instance("5678") is None
    assert not caplog.records


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_streamed_response_preserves_results_and_failures(make_vast_client, caplog, debug, interrupted):
    """Read a chunked response once and preserve transport failures with diagnostics enabled or disabled."""

    class ResponseStream(httpx.SyncByteStream):
        """Model a response whose content is not loaded when the response hook runs."""

        reads = 0
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            """Deliver a complete JSON body or time out after its first chunk."""

            self.reads += 1
            yield b'{"instances":'
            if interrupted:
                raise httpx.ReadTimeout("Stream interrupted for vast-token")
            yield b'null,"detail":"chunked-response"}'

        def close(self) -> None:
            """Record resource cleanup after consumption or failure."""

            self.closed = True

    stream = ResponseStream()

    def handler(request):
        """Return headers immediately and stream the body when the client reads it."""

        return httpx.Response(200, stream=stream)

    with caplog.at_level(logging.DEBUG if debug else logging.WARNING, logger="galloc"):
        client = make_vast_client(handler)
        if interrupted:
            with pytest.raises(APIRequestFailed, match="Vast.ai request failed"):
                client.get_instance("5678")
        else:
            assert client.get_instance("5678") is None

    assert stream.reads == 1 and stream.closed
    assert ("chunked-response" in caplog.text) == (debug and not interrupted)
    assert ("ReadTimeout" in caplog.text) == (debug and interrupted)
    assert "vast-token" not in caplog.text
