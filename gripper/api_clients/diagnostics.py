import json
import logging
from time import perf_counter
from uuid import uuid4

import httpx
from pydantic import JsonValue


logger = logging.getLogger(__name__)
REDACTED = "[REDACTED]"
SECRET_FIELD_PART_LIST = (
    "authorization",
    "cookie",
    "apikey",
    "token",
    "password",
    "secret",
    "privatekey",
    "passphrase",
    "jupyterurl",
)


def log_request(request: httpx.Request) -> None:
    """Record the prepared request before sending, with credentials removed."""

    if not logger.isEnabledFor(logging.DEBUG):
        return

    request_id = uuid4().hex[:8]
    request.extensions["gripper_trace"] = (request_id, perf_counter())
    logger.debug("[%s] REQUEST %s\n%s", request_id, _request_line(request), _message_body(request))


def log_response(response: httpx.Response) -> None:
    """Read and record the actual response before provider schema validation."""

    trace = response.request.extensions.get("gripper_trace")
    if trace is None or not logger.isEnabledFor(logging.DEBUG):
        return

    request_id, started_at = trace
    response.read()
    logger.debug(
        "[%s] RESPONSE %s HTTP %s (%.3fs)\n%s",
        request_id,
        _request_line(response.request),
        response.status_code,
        perf_counter() - started_at,
        _message_body(response),
    )


def log_failure(exception: httpx.RequestError) -> None:
    """Keep transport failures attached to their request without retrying it."""

    request = exception.request
    trace = request.extensions.get("gripper_trace")
    if trace is None or not logger.isEnabledFor(logging.DEBUG):
        return

    request_id, started_at = trace
    detail = _redact(str(exception), _api_key(request))
    logger.debug(
        "[%s] FAILED %s (%.3fs): %s %s",
        request_id,
        _request_line(request),
        perf_counter() - started_at,
        type(exception).__name__,
        json.dumps(detail),
    )


def _request_line(request: httpx.Request) -> str:
    """Show method and URL while masking URL credentials and sensitive query values."""

    api_key = _api_key(request)
    params = [
        (name, REDACTED if _is_secret(name) else _redact(value, api_key))
        for name, value in request.url.params.multi_items()
    ]
    url = request.url.copy_with(username="", password="", params=params)
    return f"{request.method} {_redact(str(url), api_key)}"


def _message_body(message: httpx.Request | httpx.Response) -> str:
    """Format headers and complete JSON or text bodies without terminal control characters."""

    request = message if isinstance(message, httpx.Request) else message.request
    api_key = _api_key(request)
    headers = json.dumps(_redact(dict(message.headers), api_key), indent=2)
    body = "(empty)"

    if message.content:
        try:
            value = json.loads(message.content)
        except ValueError:
            value = message.content.decode("utf-8", errors="replace")
        body = json.dumps(_redact(value, api_key), indent=2)

    return f"Headers: {headers}\nBody: {body}"


def _api_key(request: httpx.Request) -> str:
    """Read the bearer credential only to remove any echoed copies from diagnostics."""

    return request.headers.get("Authorization", "").removeprefix("Bearer ")


def _is_secret(name: str) -> bool:
    """Recognize credentials and environment values in headers, queries, and JSON fields."""

    name = name.lower().replace("-", "").replace("_", "")
    return name in {"key", "keys", "env", "environment"} or any(part in name for part in SECRET_FIELD_PART_LIST)


def _redact(value: JsonValue, api_key: str) -> JsonValue:
    """Mask nested secret fields and echoed credentials without changing the HTTP data."""

    if isinstance(value, dict):
        return {name: REDACTED if _is_secret(name) else _redact(item, api_key) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    if isinstance(value, str) and api_key:
        return value.replace(api_key, REDACTED)
    return value
