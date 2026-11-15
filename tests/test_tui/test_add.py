import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import httpx
from textual.widgets import DataTable, OptionList, Static

from galloc.core import storage
from galloc.tui import operations
from galloc.tui.add import AddScreen
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle


DATA_CENTER_RESPONSE = {
    "dataCenters": [
        {"id": "US-KS-2", "name": "US-KS-2", "region": "NORTH_AMERICA"},
        {"id": "EU-NL-1", "name": "EU-NL-1", "region": "EUROPE"},
    ]
}


def serve_catalog(request, pod_data):
    """Answer catalog reads, Pod creation, and the list reload that follows it."""

    if request.url.path.endswith("catalog/gpus"):
        return httpx.Response(200, json={"gpus": [make_gpu_data()]})
    if request.url.path.endswith("catalog/datacenters"):
        return httpx.Response(200, json=DATA_CENTER_RESPONSE)
    if request.method == "POST":
        return httpx.Response(201, json=pod_data)
    return httpx.Response(200, json=pod_data)


def test_empty_list_shows_the_key_that_adds_a_gpu(settings, config_path, runpod_http):
    """Keep the key line visible when there is no GPU to select."""

    config_path.touch()

    async def scenario(pilot):
        """Read the first screen without pressing any key."""

        assert "add" in pilot.app.screen.query_one("#keys", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    runpod_http.assert_not_called()


def test_add_creates_the_chosen_gpu_and_shows_it_in_the_list(settings, config_path, runpod_http, pod_data):
    """Choose the model and the second region, enter a TTL, and return to the list."""

    config_path.touch()
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)

    async def scenario(pilot):
        """Walk through GPU, region, and TTL with the keyboard."""

        await pilot.press("a")
        await settle(pilot)
        assert " $5.29/hour" in pilot.app.screen.query_one(OptionList).get_option_at_index(0).prompt

        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("down", "enter", "2", "h")
        assert "about $10.58" in pilot.app.screen.query_one("#note", Static).content

        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        assert "Created gpu-1" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    (creation_request,) = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "POST"]
    payload = json.loads(creation_request.content)
    assert (payload["gpu"], payload["dataCenterIds"]) == ({"id": "NVIDIA H200", "count": 1}, ["EU-NL-1"])
    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id == pod_data["id"]
    assert timedelta(minutes=119) < gpu.expires_at - datetime.now(UTC) <= timedelta(hours=2)


def test_invalid_ttl_keeps_the_add_screen_open(settings, config_path, runpod_http, pod_data):
    """Refuse a TTL that cannot be parsed before anything is created."""

    config_path.touch()
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)

    async def scenario(pilot):
        """Enter a duration without a unit."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "5", "enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, AddScreen)
        assert "Use a positive TTL" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)


def test_unresolved_creation_names_the_pod_to_check(settings, config_path, runpod_http, pod_data):
    """Keep the reserved mapping and say where to look when the provider gives no clear answer."""

    config_path.touch()

    def handler(request):
        """Fail only the creation request with a server error."""

        if request.method == "POST":
            return httpx.Response(500)
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Create with the first model, the first region, and one hour."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "1", "h", "enter")
        await settle(pilot)

        assert "is unresolved" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id is None


def test_add_closed_during_a_catalog_read_changes_nothing(settings, config_path, runpod_http):
    """Return to the list when the screen is cancelled before the catalog answers."""

    config_path.touch()
    answer_allowed = threading.Event()

    def handler(request):
        """Hold the catalog answer until the screen has been closed."""

        answer_allowed.wait(timeout=5)
        return httpx.Response(200, json={"gpus": [make_gpu_data()]})

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Cancel while the catalog request is still running."""

        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")
        answer_allowed.set()
        await settle(pilot)

        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)

    runpod_http.assert_called_once()


def test_quitting_during_creation_saves_the_returned_instance(settings, config_path, runpod_http, pod_data):
    """Finish an already submitted creation and save its mapping even if the interface closes."""

    config_path.touch()
    request_started = threading.Event()
    answer_allowed = threading.Event()

    def handler(request):
        """Hold only the billable request until the application has been told to quit."""

        if request.method == "POST":
            request_started.set()
            answer_allowed.wait(timeout=5)
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Quit with a creation in flight and then let the provider finish it."""

        pilot.app.screen.run_operation(operations.create, "H200", "US-KS-2", "1h")
        assert await asyncio.to_thread(request_started.wait, 5)
        try:
            await pilot.press("q")
        finally:
            answer_allowed.set()

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id == pod_data["id"]
    assert sum(call.args[0].method == "POST" for call in runpod_http.call_args_list) == 1
