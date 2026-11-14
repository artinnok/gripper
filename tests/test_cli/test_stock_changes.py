import json
from datetime import timedelta

import httpx
import pytest
import questionary

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME, TerminalInput


@pytest.fixture
def stock_catalogs():
    """Provide a selectable GPU followed by its disappearance and a cheaper alternative."""

    first = make_gpu_data(id="first", name="First GPU", price={"secure": 0.50})
    replacement = make_gpu_data(id="replacement", name="Replacement GPU", price={"secure": 0.25})
    before = {"gpus": [first, replacement]}
    after = {"gpus": [first | {"availability": "NONE", "dataCenters": []}, replacement]}
    return before, after


@pytest.fixture
def stock_regions():
    """Supply a provider region for the selectable GPUs."""

    return {"dataCenters": [{"id": "US-KS-2", "name": "Kansas", "region": "NORTH_AMERICA"}]}


@pytest.mark.parametrize("stock_scope", ["global", "regional"])
@pytest.mark.parametrize("budget", [None, "2.5"])
@pytest.mark.parametrize("fixed_region", [False, True])
def test_stock_change_preserves_limit(
    runner,
    runpod_http,
    keyboard,
    database_path,
    pod_data,
    stock_catalogs,
    stock_regions,
    budget,
    fixed_region,
    stock_scope,
):
    """Return to GPU selection after final validation, preserving TTL or recalculating the budget."""

    before, after = stock_catalogs
    target_region = "US-KS-2" if fixed_region else "EU-NL-1"
    first, replacement = before["gpus"]
    first["dataCenters"] = [{"id": "US-KS-2", "availability": "HIGH"}]
    replacement["dataCenters"] = [{"id": target_region, "availability": "LOW"}]
    stock_regions["dataCenters"].append({"id": "EU-NL-1", "name": "Netherlands", "region": "EUROPE"})
    if stock_scope == "regional":
        unavailable, _ = after["gpus"]
        unavailable["availability"] = "HIGH"
    response_list = [httpx.Response(200, json=before)]
    key_list = [b"\r"]
    argument_list = ["add", "--output", "json"]
    if fixed_region:
        argument_list += ["--region", "US-KS-2"]
    else:
        response_list += [httpx.Response(200, json=before), httpx.Response(200, json=stock_regions)]
        key_list.append(b"\r")
    if budget is None:
        key_list.append(b"\x1b[B\r")
    else:
        argument_list += ["--budget", budget]
    response_list.append(httpx.Response(200, json=after))
    if stock_scope == "regional":
        response_list.append(httpx.Response(200, json=stock_regions))
    response_list.append(httpx.Response(200, json=after))
    key_list.append(b"\r")
    if not fixed_region:
        response_list += [httpx.Response(200, json=after), httpx.Response(200, json=stock_regions)]
        key_list.append(b"\r")
    pod_data["gpu"]["id"] = "replacement"
    pod_data["dataCenterId"] = target_region
    runpod_http.side_effect = response_list + [
        httpx.Response(200, json=after),
        httpx.Response(200, json=stock_regions),
        httpx.Response(201, json=pod_data),
    ]
    keyboard.send_bytes(b"".join(key_list) + b"\x03")

    result = runner.invoke(cli, argument_list, input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert "First GPU is currently unavailable" in result.stderr
    assert "Choose another GPU." in result.stderr
    assert json.loads(result.stdout)["instance"]["gpu"] == "replacement"
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.expires_at - CREATION_TIME == timedelta(hours=1 if budget is None else 10)
    request_list = [request for (request,), _ in runpod_http.call_args_list if request.method == "POST"]
    (request,) = request_list
    assert json.loads(request.content)["gpu"]["id"] == "replacement"
    assert json.loads(request.content)["dataCenterIds"] == [target_region]


def test_stock_change_before_region_returns_to_gpu(
    runner, runpod_http, keyboard, pod_data, stock_catalogs, stock_regions
):
    """Keep the recorded video scenario inside the picker when stock disappears before region selection."""

    before, after = stock_catalogs
    runpod_http.side_effect = [
        httpx.Response(200, json=before),
        httpx.Response(200, json=after),
        httpx.Response(200, json=after),
        httpx.Response(200, json=after),
        httpx.Response(200, json=stock_regions),
        httpx.Response(200, json=after),
        httpx.Response(200, json=stock_regions),
        httpx.Response(201, json=pod_data),
    ]
    keyboard.send_bytes(b"\r\r\r\x03")

    result = runner.invoke(cli, ["add", "--ttl", "30m"], input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert "Choose another GPU." in result.stderr
    (request,) = runpod_http.call_args.args
    assert json.loads(request.content)["gpu"]["id"] == "replacement"


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_stock_change_can_end_without_creation(
    runner, runpod_http, keyboard, database_path, stock_catalogs, empty_catalog, monkeypatch
):
    """Allow cancellation after refresh and exit clearly when no alternatives remain."""

    before, after = stock_catalogs
    refreshed = {"gpus": []} if empty_catalog else after
    runpod_http.side_effect = [
        httpx.Response(200, json=before),
        httpx.Response(200, json=after),
        httpx.Response(200, json=refreshed),
    ]
    select_prompt = questionary.select
    key_iterator = iter([b"\r", b"\x1b"])

    def make_select_prompt(*args, **kwargs):
        """Send each key only after its selector opens, matching a user's separate actions."""

        question = select_prompt(*args, **kwargs)
        question.application.pre_run_callables.append(lambda: keyboard.send_bytes(next(key_iterator)))
        return question

    monkeypatch.setattr(questionary, "select", make_select_prompt)

    result = runner.invoke(cli, ["add", "--ttl", "30m"], input=TerminalInput())

    assert result.exit_code == 1
    assert ("No GPUs currently available" if empty_catalog else "Aborted!") in result.stderr
    assert runpod_http.call_count == 3
    assert all(request.method == "GET" for (request,), _ in runpod_http.call_args_list)
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_api_failure_does_not_restart_selection(runner, runpod_http, keyboard, stock_catalogs, status_code):
    """Keep request failures distinct from confirmed lack of GPU capacity."""

    before, _ = stock_catalogs
    runpod_http.side_effect = [httpx.Response(200, json=before), httpx.Response(status_code)]
    keyboard.send_bytes(b"\r\x03")

    result = runner.invoke(cli, ["add", "--ttl", "30m"], input=TerminalInput())

    assert result.exit_code == 1
    assert f"HTTP {status_code}" in result.stderr
    assert "Choose another GPU." not in result.stderr
    assert runpod_http.call_count == 2
