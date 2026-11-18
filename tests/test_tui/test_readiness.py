import asyncio
import threading
from unittest.mock import Mock

import httpx
import pytest
from rich.spinner import Spinner
from textual.widgets import DataTable, Static

from galloc.core import storage
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.startup import StartupScreen
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


def test_startup_shows_spinner_until_runtime_and_ssh_are_ready(settings, config_path, runpod_http, pod_data):
    """Keep startup visible and responsive until Runpod supplies complete readiness evidence."""

    config_path.touch()
    settings.startup_poll_interval = 0.01
    status_requested = threading.Event()
    ready = threading.Event()

    def handler(request):
        """Report running without SSH until the test releases the last startup condition."""

        if request.method == "POST":
            return httpx.Response(201, json=pod_data | {"runtime": None})
        if "/pods/" in request.url.path and not ready.is_set():
            status_requested.set()
            return httpx.Response(200, json=pod_data | {"ssh": {"direct": None, "proxy": None}})
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Observe startup before allowing it to finish and reveal the SSH command."""

        pilot.app.screen._create_gpu(("runpod", "H200", "US-KS-2", "1h", None))
        assert await asyncio.to_thread(status_requested.wait, 5)
        await pilot.pause()
        assert isinstance(pilot.app.screen, StartupScreen)
        assert isinstance(pilot.app.screen.query_one("#startup", Static).content, Spinner)
        assert "ready" not in pilot.app.screen.query_one(Message).content
        ready.set()
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert "gpu-1 ready" in pilot.app.screen.query_one(Message).content
        assert "ssh root@" in pilot.app.screen.query_one("#info", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    assert sum(call.args[0].method == "POST" for call in runpod_http.call_args_list) == 1


@pytest.mark.parametrize("response", [httpx.Response(404), httpx.Response(500), "EXITED", "ERROR"])
def test_startup_failure_keeps_mapping_and_shows_error(settings, config_path, runpod_http, pod_data, response):
    """Do not call a failed or absent runtime ready, remove it, or repeat billable creation."""

    config_path.touch()

    def handler(request):
        """Create successfully, then return the selected startup failure."""

        if request.method == "POST":
            return httpx.Response(201, json=pod_data | {"runtime": None})
        if "/pods/" in request.url.path:
            if isinstance(response, str):
                return httpx.Response(200, json=pod_data | {"status": response})
            return response
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Wait for the failed status check and inspect its inline error."""

        pilot.app.screen._create_gpu(("runpod", "H200", "US-KS-2", "1h", None))
        await settle(pilot)
        assert isinstance(pilot.app.screen, StartupScreen)
        assert "gpu-1:" in pilot.app.screen.query_one(Message).content
        assert not pilot.app.screen.query_one("#startup", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, "gpu-1").provider_instance_id == pod_data["id"]
    assert sum(call.args[0].method == "POST" for call in runpod_http.call_args_list) == 1
    assert all(call.args[0].method != "DELETE" for call in runpod_http.call_args_list)


def test_startup_timeout_uses_configured_limit(settings, config_path, runpod_http, pod_data, monkeypatch):
    """End the wait at the configured deadline without losing the created GPU."""

    config_path.touch()
    settings.startup_timeout = 1
    monkeypatch.setattr("galloc.core.readiness.monotonic", Mock(side_effect=[0, 2]))
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data | {"runtime": None})

    async def scenario(pilot):
        """Observe a named timeout instead of a ready result."""

        pilot.app.screen._create_gpu(("runpod", "H200", "US-KS-2", "1h", None))
        await settle(pilot)
        assert "not ready after 1 seconds" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, "gpu-1").provider_instance_id == pod_data["id"]


def test_escape_stops_waiting_without_deleting_gpu(settings, config_path, runpod_http, pod_data):
    """Cancel a long polling sleep immediately while preserving the mapped instance."""

    config_path.touch()
    settings.startup_poll_interval = 60
    status_requested = threading.Event()

    def handler(request):
        """Leave startup pending and let the list read the same provisioning state."""

        if "/pods/" in request.url.path:
            status_requested.set()
        return serve_catalog(request, pod_data | {"runtime": None})

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Return to the GPU list while the startup worker is sleeping."""

        pilot.app.screen._create_gpu(("runpod", "H200", "US-KS-2", "1h", None))
        assert await asyncio.to_thread(status_requested.wait, 5)
        await pilot.press("escape")
        await asyncio.wait_for(settle(pilot), timeout=3)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        assert "stopped waiting" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, "gpu-1").provider_instance_id == pod_data["id"]
    assert all(call.args[0].method != "DELETE" for call in runpod_http.call_args_list)
