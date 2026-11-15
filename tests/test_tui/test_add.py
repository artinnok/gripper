import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from textual.widgets import DataTable, Input, OptionList, Static

from galloc.core import storage
from galloc.tui.add import AddScreen
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.startup import StartupScreen
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
        choice = pilot.app.screen.query_one(OptionList)
        row = choice.get_option_at_index(0).prompt
        columns = pilot.app.screen.query_one("#columns", Static).content
        assert columns.split() == ["GPU", "USD/HOUR", "VRAM", "(GB)", "STOCK"]
        assert row.index("$5.29") == columns.index("USD/HOUR")
        assert row.index("141") == columns.index("VRAM")
        assert row.index("High") == columns.index("STOCK")

        await pilot.press("enter")
        await settle(pilot)
        assert pilot.app.screen.query_one("#columns", Static).content.split() == ["ID", "LOCATION"]
        await pilot.press("down", "enter", "enter", "2", "h")
        assert not pilot.app.screen.query_one("#columns", Static).display
        assert "about $10.58" in pilot.app.screen.query_one("#note", Static).content

        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        assert "gpu-1 ready" in pilot.app.screen.query_one(Message).content

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
        await pilot.press("enter", "enter", "5", "enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, AddScreen)
        assert "Use a positive TTL" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)


def test_refresh_replaces_the_catalog_and_marks_cheap_gpus(settings, config_path, runpod_http):
    """Reload current models and prices without duplicating rows or marking the threshold itself."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json={"gpus": [make_gpu_data()]}),
        httpx.Response(
            200,
            json={
                "gpus": [
                    make_gpu_data(id="NVIDIA RTX 2000 Ada", name="RTX 2000 Ada", price={"secure": 0.39}),
                    make_gpu_data(id="NVIDIA RTX A4000", name="RTX A4000", price={"secure": 0.40}),
                ]
            },
        ),
    ]

    async def scenario(pilot):
        """Refresh the open GPU picker and inspect the new rows."""

        await pilot.press("a")
        await settle(pilot)
        choice = pilot.app.screen.query_one(OptionList)
        assert choice.option_count == 1
        assert "refresh" in pilot.app.screen.query_one("#keys", Static).content

        await pilot.press("r")
        await settle(pilot)
        assert choice.option_count == 2
        assert not choice.disabled
        regular, cheap = (choice.get_option_at_index(index).prompt for index in range(2))
        assert "RTX A4000" in regular and "$0.40" in regular and "ϟ" not in regular
        assert "RTX 2000 Ada" in cheap and "$0.39" in cheap and "ϟ" in cheap
        assert regular.index("$") == cheap.index("$")

    run_app(GallocApp(settings, config_path), scenario)
    assert runpod_http.call_count == 2


def test_failed_catalog_refresh_can_be_retried(settings, config_path, runpod_http):
    """Clear stale choices on failure and allow another refresh from the same screen."""

    config_path.touch()
    catalog = {"gpus": [make_gpu_data()]}
    runpod_http.side_effect = [
        httpx.Response(200, json=catalog),
        httpx.Response(500),
        httpx.Response(200, json=catalog),
    ]

    async def scenario(pilot):
        """Fail a refresh, then reload successfully without leaving the picker."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("r")
        await settle(pilot)
        choice = pilot.app.screen.query_one(OptionList)
        assert choice.option_count == 0
        assert "500" in pilot.app.screen.query_one(Message).content

        await pilot.press("r")
        await settle(pilot)
        assert choice.option_count == 1
        assert not choice.disabled
        assert pilot.app.screen.query_one(Message).content == ""

    run_app(GallocApp(settings, config_path), scenario)
    assert runpod_http.call_count == 3


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
        await pilot.press("enter", "enter", "1", "h", "enter")
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

        pilot.app.push_screen(StartupScreen("H200", "US-KS-2", "1h", None))
        assert await asyncio.to_thread(request_started.wait, 5)
        try:
            await pilot.press("ctrl+q")
            assert isinstance(pilot.app.screen, GPUListScreen)
            assert "will remain billable" in pilot.app.screen.query_one("#info", Static).content
            await pilot.press("enter")
        finally:
            answer_allowed.set()

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.provider_instance_id == pod_data["id"]
    assert sum(call.args[0].method == "POST" for call in runpod_http.call_args_list) == 1


def test_fractional_budget_previews_ttl_and_recalculates_before_creation(settings, config_path, runpod_http, pod_data):
    """Round down a fractional budget using the latest price when creation is validated."""

    config_path.touch()
    catalog_reads = 0

    def handler(request):
        """Change the price between the displayed catalog and final validation."""

        nonlocal catalog_reads
        if request.url.path.endswith("catalog/gpus"):
            catalog_reads += 1
            price = 5.29 if catalog_reads < 3 else 10
            return httpx.Response(200, json={"gpus": [make_gpu_data(price={"secure": price})]})
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Choose Budget, inspect its preview, and submit a decimal amount."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "down", "enter", "2", ".", "5")
        assert "28m" in pilot.app.screen.query_one("#note", Static).content
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert timedelta(minutes=14) < gpu.expires_at - datetime.now(UTC) <= timedelta(minutes=15)
    assert sum(call.args[0].method == "POST" for call in runpod_http.call_args_list) == 1


@pytest.mark.parametrize("budget", ["0", "-1", "NaN", "0.001"])
def test_invalid_budget_keeps_input_editable(settings, config_path, runpod_http, pod_data, budget):
    """Reject nonpositive, nonfinite, and sub-minute amounts without creating a Pod."""

    config_path.touch()
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)

    async def scenario(pilot):
        """Enter an invalid budget and then cancel the unchanged creation flow."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "down", "enter")
        pilot.app.screen.query_one(Input).value = budget
        await pilot.press("enter")
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.query_one(Message).content
        assert not pilot.app.screen.query_one(Input).disabled
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
