import base64

import httpx
import pytest
from textual.widgets import Static

from galloc.core import storage
from galloc.core.schemas import GPU
from galloc.tui.app import GallocApp
from galloc.tui.message import Message
from tests.test_tui.conftest import run_app, settle


def test_copy_ssh_uses_selected_gpu_and_full_command(settings, config_path, runpod_http, gpu, pod_data, monkeypatch):
    """Send the full command to the terminal clipboard, even when the detail column wraps it."""

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
        """Change the selected row, then copy through the keyboard shortcut."""

        await pilot.press("down")
        info = pilot.app.screen.query_one("#info", Static)
        assert "gpu-2" in info.content and command in info.content
        assert len(command) > info.content_size.width
        output_list = []
        monkeypatch.setattr(pilot.app._driver, "write", output_list.append)
        await pilot.press("c")
        assert pilot.app.clipboard == command
        payload = base64.b64encode(command.encode()).decode()
        assert f"\x1b]52;c;{payload}\a" in output_list
        assert "SSH command copied" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)
    assert runpod_http.call_count == 2


def test_copy_ssh_keeps_clipboard_when_connection_or_gpu_is_unavailable(
    settings, config_path, runpod_http, gpu, pod_data
):
    """Do not overwrite copied text with a placeholder or a stale command after refresh fails."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json=pod_data | {"ssh": {"direct": None, "proxy": None}}),
        httpx.Response(500),
    ]

    async def scenario(pilot):
        """Try an instance without SSH, then an empty list after a provider failure."""

        pilot.app.copy_to_clipboard("previous clipboard")
        await pilot.press("c")
        assert pilot.app.clipboard == "previous clipboard"
        assert "SSH is not available" in pilot.app.screen.query_one(Message).content
        await pilot.press("r")
        await settle(pilot)
        await pilot.press("c")
        assert pilot.app.clipboard == "previous clipboard"

    run_app(GallocApp(settings, config_path), scenario)


@pytest.mark.parametrize("key", ["e", "d", "q"])
def test_copy_shortcut_does_not_interrupt_questions(settings, config_path, runpod_http, gpu, pod_data, key):
    """Keep copy inactive while the detail column asks for a duration, deletion, or exit."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]

    async def scenario(pilot):
        """Open each question and ensure c cannot replace the clipboard or close it."""

        await pilot.press(key)
        question = pilot.app.screen.pending_action
        await pilot.press("c")
        assert pilot.app.clipboard == ""
        assert pilot.app.screen.pending_action == question

    run_app(GallocApp(settings, config_path), scenario)
