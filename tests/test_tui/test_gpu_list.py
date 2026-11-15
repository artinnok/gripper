import asyncio
import threading
from datetime import timedelta

import httpx
from textual.widgets import DataTable, Input, Static

from galloc.core import storage
from galloc.tui.app import GallocApp
from galloc.tui.message import Message
from tests.test_tui.conftest import run_app, settle


def test_list_shows_live_status_beside_details(settings, config_path, runpod_http, gpu, pod_data):
    """Fill the table and the detail column from the provider response."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json=pod_data),
        httpx.Response(200, json=pod_data | {"cost": 2.50}),
    ]

    async def scenario(pilot):
        """Read live details and refresh the provider's hourly rate."""

        (name, status, time_left, region, model) = pilot.app.screen.query_one(DataTable).get_row_at(0)
        assert (name, region, model) == ("gpu-1", "US-KS-2", "NVIDIA H200")
        assert "running" in status
        assert "ssh root@195.26.233.3 -p 34446" in pilot.app.screen.query_one("#info", Static).content
        assert "$3.99/hour" in pilot.app.screen.query_one("#info", Static).content

        await pilot.press("r")
        await settle(pilot)
        assert "$2.50/hour" in pilot.app.screen.query_one("#info", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    assert runpod_http.call_count == 2


def test_extend_adds_time_to_the_saved_deadline(settings, config_path, runpod_http, gpu, pod_data):
    """Extend the selected GPU from the prompt and keep the list open."""

    config_path.touch()
    runpod_http.side_effect = lambda request: httpx.Response(200, json=pod_data)

    async def scenario(pilot):
        """Type a duration into the prompt and confirm it."""

        await pilot.press("e", "1", "h")
        assert "new deadline" in pilot.app.screen.query_one("#preview", Static).content
        await pilot.press("enter")
        await settle(pilot)
        assert not pilot.app.screen.query_one(Input).display

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, gpu.id).expires_at == gpu.expires_at + timedelta(hours=1)


def test_delete_runs_only_after_confirmation(settings, config_path, runpod_http, gpu, pod_data):
    """Keep the GPU until Enter confirms, then delete it and empty the list."""

    config_path.touch()

    def handler(request):
        """Serve the Pod until its deletion is requested."""

        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Try another action, cancel, then confirm."""

        await pilot.press("d", "e")
        assert not pilot.app.screen.query_one(Input).display

        await pilot.press("escape", "enter")
        await settle(pilot)
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)

        await pilot.press("d", "enter")
        await settle(pilot)
        assert pilot.app.screen.query_one(DataTable).row_count == 0
        assert "No GPUs." in pilot.app.screen.query_one("#info", Static).content
        assert "Deleted gpu-1" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    assert runpod_http.call_args_list[-1].args[0].method == "DELETE"
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_provider_failure_keeps_the_mapping(settings, config_path, runpod_http, gpu):
    """Show the failure beside an empty list without treating it as absence."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(500)]

    async def scenario(pilot):
        """Read the failure, then press a key that starts no request."""

        message = pilot.app.screen.query_one(Message)
        assert pilot.app.screen.query_one(DataTable).row_count == 0
        assert "500" in message.content

        await pilot.press("down")
        assert message.content == ""

    run_app(GallocApp(settings, config_path), scenario)

    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [gpu]


def test_failed_refresh_hides_stale_status_and_can_reload(settings, config_path, runpod_http, gpu, pod_data):
    """Keep mappings on failure without presenting an earlier response as current state."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json=pod_data),
        httpx.Response(500),
        httpx.Response(200, json=pod_data),
    ]

    async def scenario(pilot):
        """Fail an update after a successful read, then restore provider access."""

        screen = pilot.app.screen
        assert screen.query_one(DataTable).row_count == 1

        await pilot.press("r")
        await settle(pilot)
        assert screen.query_one(DataTable).row_count == 0
        assert "unavailable" in screen.query_one("#info", Static).content
        with storage.open_database(settings.database_path) as connection:
            assert storage.get_gpu_list(connection) == [gpu]

        await pilot.press("r")
        await settle(pilot)
        assert screen.query_one(DataTable).row_count == 1

    run_app(GallocApp(settings, config_path), scenario)


def test_superseded_refresh_failure_does_not_replace_newer_result(settings, config_path, runpod_http, gpu, pod_data):
    """Discard an old request's failure after a newer refresh has already succeeded."""

    config_path.touch()
    runpod_http.return_value = httpx.Response(200, json=pod_data)
    runpod_http.side_effect = None
    request_started = threading.Event()
    answer_allowed = threading.Event()
    request_finished = threading.Event()

    def delayed_failure(request):
        """Release the old error only after the new result is visible."""

        request_started.set()
        answer_allowed.wait(timeout=5)
        request_finished.set()
        return httpx.Response(500)

    async def scenario(pilot):
        """Start a delayed refresh and supersede it with a successful one."""

        runpod_http.side_effect = delayed_failure
        pilot.app.screen.refresh_gpu_list()
        assert await asyncio.to_thread(request_started.wait, 5)
        try:
            runpod_http.side_effect = None
            pilot.app.screen.refresh_gpu_list()
            await settle(pilot)
        finally:
            answer_allowed.set()
        assert await asyncio.to_thread(request_finished.wait, 5)
        await pilot.pause()

        assert pilot.app.screen.query_one(DataTable).row_count == 1
        assert pilot.app.screen.query_one(Message).content == ""

    run_app(GallocApp(settings, config_path), scenario)
