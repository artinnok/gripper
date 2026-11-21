import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from textual.widgets import DataTable, Static

from galloc.core import storage
from galloc.core.schemas import GPU
from galloc.tui.app import GallocApp
from galloc.tui.message import Message
from tests.test_tui.conftest import run_app, settle


def test_ssh_opens_the_selected_gpu(settings, config_path, runpod_http, gpu, pod_data, ssh_session):
    """Open the selected GPU even when its connection details wrap in the sidebar."""

    config_path.touch()
    other_gpu = GPU(provider="runpod", provider_instance_id="other-pod", expires_at=gpu.expires_at)
    command = "ssh root@some-long-hostname.example.test -p 12345"
    other_pod = pod_data | {"id": "other-pod", "ssh": {"direct": {"command": command}, "proxy": None}}
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, other_gpu)
    runpod_http.side_effect = lambda request: httpx.Response(
        200, json=other_pod if request.url.path.endswith("other-pod") else pod_data
    )

    async def scenario(pilot):
        """Select the second GPU, open SSH, and resume navigation."""

        await pilot.press("down")
        info = pilot.app.screen.query_one("#info", Static)
        assert "gpu-2" in info.content
        assert command in info.content
        assert len(command) > info.content_size.width
        await pilot.press("c")
        ssh_session.assert_not_awaited()
        await pilot.press("s")
        ssh_session.assert_awaited_once_with(["ssh", "root@some-long-hostname.example.test", "-p", "12345"])
        await pilot.press("up")
        assert "gpu-1" in info.content

    run_app(GallocApp(settings, config_path), scenario)
    assert runpod_http.call_count == 2


def test_ssh_requires_a_selected_gpu_with_a_connection(settings, config_path, runpod_http, gpu, pod_data, ssh_session):
    """Never launch SSH for a missing endpoint or a stale selection after refresh fails."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json=pod_data | {"ssh": {"direct": None, "proxy": None}}),
        httpx.Response(500),
    ]

    async def scenario(pilot):
        """Try an instance without SSH, then an empty list after a provider failure."""

        await pilot.press("s")
        ssh_session.assert_not_awaited()
        assert " SSH" not in pilot.app.screen.query_one("#keys", Static).content
        assert pilot.app.screen.query_one(Message).content == ""
        await pilot.press("r")
        await settle(pilot)
        await pilot.press("s")
        ssh_session.assert_not_awaited()

    run_app(GallocApp(settings, config_path), scenario)


@pytest.mark.parametrize("key", ["e", "d", "q"])
def test_ssh_does_not_interrupt_questions(settings, config_path, runpod_http, gpu, pod_data, key, ssh_session):
    """Keep SSH inactive while the detail column asks for a duration, deletion, or exit."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]

    async def scenario(pilot):
        """Keep the question open when s is pressed."""

        await pilot.press(key)
        question = pilot.app.screen.pending_action
        await pilot.press("s")
        ssh_session.assert_not_awaited()
        assert pilot.app.screen.pending_action == question

    run_app(GallocApp(settings, config_path), scenario)


@pytest.mark.parametrize("outcome, reason", [(255, "status 255"), (FileNotFoundError("ssh missing"), "ssh missing")])
def test_ssh_failure_returns_to_the_list(
    settings, config_path, runpod_http, gpu, pod_data, ssh_session, outcome, reason
):
    """Keep the dashboard usable when the client cannot start or connect."""

    config_path.touch()
    runpod_http.return_value = httpx.Response(200, json=pod_data)
    runpod_http.side_effect = None
    ssh_session.side_effect = [outcome]

    async def scenario(pilot):
        """Open the connection, read the failure and navigate to SSH keys."""

        await pilot.press("s")
        assert reason in pilot.app.screen.query_one(Message).content
        await pilot.press("k")
        assert pilot.app.screen is not pilot.app.gpu_screen

    run_app(GallocApp(settings, config_path), scenario)


def test_ttl_runs_during_ssh(settings, config_path, runpod_http, gpu, pod_data, ssh_session, monkeypatch):
    """Enforce a deadline while the interactive SSH session still owns the terminal."""

    config_path.touch()
    monkeypatch.setattr("galloc.tui.app.TTL_CHECK_INTERVAL", 0.05)

    def serve(request):
        """Confirm the deletion while the SSH client is waiting."""

        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = serve

    async def wait_for_deletion(argument_list):
        """Make the deadline due and wait for the TTL worker to remove the mapping."""

        gpu.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with storage.open_database(settings.database_path) as connection:
            storage.update_gpu_expiration(connection, gpu)
            async with asyncio.timeout(5):
                while storage.get_gpu_list(connection):
                    await asyncio.sleep(0.02)
        return 0

    ssh_session.side_effect = wait_for_deletion

    async def scenario(pilot):
        """Return from the session to the provider-confirmed empty list."""

        await pilot.press("s")
        await settle(pilot)
        ssh_session.assert_awaited_once()
        assert pilot.app.screen.query_one(DataTable).row_count == 0

    run_app(GallocApp(settings, config_path), scenario)
    assert sum(call.args[0].method == "DELETE" for call in runpod_http.call_args_list) == 1
