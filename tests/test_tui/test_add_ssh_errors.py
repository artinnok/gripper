import asyncio
import json
import threading

import httpx
from textual.widgets import Input, OptionList, Static

from gripper.tui.add import AddScreen
from gripper.tui.add_catalog import GPUCatalog
from gripper.tui.add_steps import AddStep
from gripper.tui.app import GripperApp
from gripper.tui.message import Message
from gripper.tui.ssh_key.add import SSHKeyAddScreen
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


def test_failed_key_lookup_stops_creation_and_can_be_retried(settings, config_path, runpod_http, pod_data):
    """Keep a denied account-key read separate from an empty account, with no billable request."""

    config_path.touch()
    denied = True

    def handler(request):
        """Deny only key discovery until the test permits the next read."""

        if denied and request.url.path.endswith("account/ssh-keys"):
            return httpx.Response(403)
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Observe the inline failure, retry it, then cancel the key chooser back to GPU selection."""

        nonlocal denied
        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.SSH_KEY
        assert "403" in pilot.app.screen.query_one(Message).content
        await pilot.press("enter")
        assert pilot.app.screen.step == AddStep.SSH_KEY
        denied = False
        await pilot.press("r")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyAddScreen)
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.GPU

    run_app(GripperApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)


def test_registration_failure_preserves_generated_pair_and_allows_retry(
    settings, config_path, runpod_http, pod_data, home
):
    """Keep a generated key on upload failure and never proceed to renting before registration succeeds."""

    config_path.touch()
    denied = True
    registered_key_list = []
    private_path = home / ".ssh" / "gripper_ed25519"

    def handler(request):
        """Fail the first key upload and accept a later retry without creating any GPU."""

        if request.url.path.endswith("account/ssh-keys"):
            if request.method == "PUT":
                if denied:
                    return httpx.Response(403)
                registered_key_list[:] = json.loads(request.content)["keys"]
            return httpx.Response(200, json={"keys": registered_key_list})
        assert request.method == "GET"
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Generate, retry the same public file after failure, then reach the region step."""

        nonlocal denied
        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("end", "enter", "enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyAddScreen)
        assert "403" in pilot.app.screen.query_one(Message).content
        assert not pilot.app.screen.query_one(Input).disabled
        private_contents = private_path.read_bytes()
        denied = False
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.REGION
        assert private_path.read_bytes() == private_contents
        assert len(registered_key_list) == 1

    run_app(GripperApp(settings, config_path), scenario)


def test_leaving_during_key_lookup_ignores_its_late_answer(settings, config_path, runpod_http, pod_data):
    """Do not open key setup after the user has cancelled the GPU choice whose access was being checked."""

    config_path.touch()
    request_started = threading.Event()
    answer_allowed = threading.Event()

    def handler(request):
        """Hold the account-key answer until the user returns to GPU selection."""

        if request.url.path.endswith("account/ssh-keys"):
            request_started.set()
            answer_allowed.wait(timeout=5)
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Step back before the key read finishes and ensure it cannot move the screen forward."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        assert await asyncio.to_thread(request_started.wait, 5)
        await pilot.pause()
        info = pilot.app.screen.query_one("#info", Static)
        assert "checking SSH key" in info.render_line(2).text
        try:
            screen = pilot.app.screen
            assert screen.query_one("#progress", Static).content.endswith("GPU")
            assert screen.query_one(GPUCatalog).display
            assert screen.query_one("#choice", OptionList).option_count > 0
            await pilot.press("enter", "r", "escape")
        finally:
            answer_allowed.set()
        await settle(pilot)
        assert info.size.height == 1
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.GPU
        assert pilot.app.screen.query_one(GPUCatalog).display
        assert not pilot.app.screen.query_one("#choice", OptionList).disabled

    run_app(GripperApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
