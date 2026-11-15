import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest
from textual.widgets import DataTable, Static

from galloc.core import storage
from galloc.tui.app import GallocApp
from galloc.tui.message import Message
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle


def test_startup_removes_overdue_gpus(settings, config_path, expired_gpu, pod_data, runpod_http):
    """Process an overdue deadline immediately and remove its row after provider confirmation."""

    config_path.touch()

    def handler(request):
        """Allow the initial list read and confirm deletion without touching a real provider."""

        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Wait for the deletion-triggered refresh and inspect the current screen."""

        await settle(pilot)
        assert pilot.app.screen.query_one(DataTable).row_count == 0
        assert "TTL: deleted gpu-1" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == []
    assert sum(call.args[0].method == "DELETE" for call in runpod_http.call_args_list) == 1


@pytest.mark.parametrize("key", ["a", "e", "q", "k"])
def test_deadline_is_enforced_while_other_screens_or_prompts_are_open(
    settings, config_path, gpu, pod_data, runpod_http, monkeypatch, key
):
    """Keep TTL processing active in the add flow, extension prompt, and exit confirmation."""

    config_path.touch()
    monkeypatch.setattr("galloc.tui.app.TTL_CHECK_INTERVAL", 0.05)

    def handler(request):
        """Serve screen reads and confirm the only automatic deletion."""

        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("catalog/gpus"):
            return httpx.Response(200, json={"gpus": [make_gpu_data()]})
        if request.url.path.endswith("account/ssh-keys"):
            return httpx.Response(200, json={"keys": []})
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Let a deadline arrive while the list is covered or waiting for input."""

        await pilot.press(key)
        await settle(pilot)
        gpu.expires_at = datetime.now(UTC) + timedelta(seconds=0.1)
        with storage.open_database(settings.database_path) as connection:
            storage.update_gpu_expiration(connection, gpu)
        await pilot.pause(0.3)
        await settle(pilot)
        with storage.open_database(settings.database_path) as connection:
            assert storage.get_gpu_list(connection) == []
        assert pilot.app.return_code is None
        assert pilot.app.gpu_screen.query_one(DataTable).row_count == 0

    run_app(GallocApp(settings, config_path), scenario)
    assert sum(call.args[0].method == "DELETE" for call in runpod_http.call_args_list) == 1


def test_failed_expiration_is_visible_and_retried(settings, config_path, expired_gpu, pod_data, runpod_http):
    """Keep a failed GPU visible, then remove it after provider access returns."""

    config_path.touch()
    deletion_status = 500

    def handler(request):
        """Fail deletion once while keeping instance reads available."""

        if request.method == "DELETE":
            return httpx.Response(deletion_status)
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Observe the inline failure and retry after connectivity is restored."""

        nonlocal deletion_status
        assert "TTL gpu-1:" in pilot.app.screen.query_one(Message).content
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        deletion_status = 204
        pilot.app.check_expiration()
        await settle(pilot)
        await settle(pilot)
        assert pilot.app.screen.query_one(DataTable).row_count == 0

    run_app(GallocApp(settings, config_path), scenario)


def test_confirmed_exit_finishes_only_the_submitted_deletion(settings, config_path, expired_gpu, pod_data, runpod_http):
    """Stay responsive during deletion, avoid overlapping passes, and leave remaining GPUs on exit."""

    config_path.touch()
    other_gpu = expired_gpu.model_copy(update={"id": uuid4(), "provider_instance_id": "another-pod"})
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, other_gpu)
    deletion_started, allow_deletion = Event(), Event()

    def handler(request):
        """Hold the first deletion open until the user has confirmed quitting."""

        if request.method == "DELETE":
            deletion_started.set()
            assert allow_deletion.wait(5)
            return httpx.Response(204)
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler
    app = GallocApp(settings, config_path)

    async def scenario():
        """Confirm exit before releasing the request that is already in flight."""

        try:
            async with app.run_test(size=(120, 30)) as pilot:
                assert await asyncio.to_thread(deletion_started.wait, 5)
                worker = app.expiration_worker
                app.check_expiration()
                assert app.expiration_worker is worker
                await pilot.press("q")
                assert "TTL monitoring will stop" in app.screen.query_one("#info", Static).content
                await pilot.press("enter")
                assert app.return_code == 0
        finally:
            allow_deletion.set()

    asyncio.run(scenario())
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [other_gpu]
    assert sum(call.args[0].method == "DELETE" for call in runpod_http.call_args_list) == 1
