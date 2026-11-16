import asyncio
import json
import threading

import httpx
from textual.widgets import Input, OptionList

from galloc.core.ssh_keys import read_public_key
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.setup import SetupScreen, SetupStep
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_setup import DATA_CENTER_RESPONSE


async def enter_credentials(pilot):
    """Reach the disk field through the real provider and credential steps."""

    await pilot.press("enter")
    pilot.app.screen.query_one(Input).value = "entered-token"
    await pilot.press("enter")
    await settle(pilot)


def test_invalid_disk_stays_editable_before_registration(settings, config_path, runpod_http):
    """Reject empty, nonpositive, fractional, and nonnumeric disk sizes before SSH side effects."""

    runpod_http.side_effect = [httpx.Response(200, json=DATA_CENTER_RESPONSE)]

    async def scenario(pilot):
        """Correct invalid values without restarting setup or losing the checked credentials."""

        await enter_credentials(pilot)
        screen = pilot.app.screen
        prompt = screen.query_one(Input)
        for value in ("", "0", "-1", "1.5", "abc"):
            prompt.value = value
            await pilot.press("enter")
            assert screen.step == SetupStep.DISK
            assert "positive whole number" in screen.query_one(Message).content
            assert prompt.has_focus and not prompt.disabled
            assert runpod_http.call_count == 1
            assert not config_path.exists()
        prompt.value = "80"
        await pilot.press("enter")
        assert screen.step == SetupStep.SSH_KEY
        assert screen.query_one(OptionList).has_focus

    run_app(GallocApp(settings, config_path), scenario)


def test_invalid_custom_key_keeps_path_editable(settings, config_path, runpod_http, home):
    """Reject empty, missing, and non-public-key files before contacting the account API."""

    (home / "invalid.pub").write_text("not an OpenSSH public key")
    runpod_http.side_effect = [httpx.Response(200, json=DATA_CENTER_RESPONSE)]

    async def scenario(pilot):
        """Fix a path inline or go back to the key list and cancel setup."""

        await enter_credentials(pilot)
        await pilot.press("enter", "enter")
        screen = pilot.app.screen
        prompt = screen.query_one(Input)
        for value in ("", "~/missing.pub", "~/invalid.pub"):
            prompt.value = value
            await pilot.press("enter")
            await settle(pilot)
            assert screen.step == SetupStep.KEY_PATH
            assert screen.query_one(Message).content
            assert prompt.has_focus and not prompt.disabled
        await pilot.press("escape")
        assert screen.query_one(OptionList).has_focus
        await pilot.press("escape")

    run_app(GallocApp(settings, config_path), scenario)
    assert not config_path.exists()
    assert runpod_http.call_count == 1


def test_generated_key_survives_registration_retry(settings, config_path, runpod_http, home):
    """Reuse a generated pair after missing permissions instead of trying to overwrite it."""

    private_path = home / ".ssh" / "galloc_ed25519"
    public_path = private_path.with_suffix(".pub")
    runpod_http.side_effect = [httpx.Response(200, json=DATA_CENTER_RESPONSE), httpx.Response(403)]

    async def scenario(pilot):
        """Generate a key, observe the refusal, then register the same key after access is restored."""

        await enter_credentials(pilot)
        await pilot.press("enter", "end", "enter")
        await settle(pilot)
        screen = pilot.app.screen
        assert "403" in screen.query_one(Message).content
        assert not config_path.exists()
        assert screen.query_one(Input).value == str(public_path)
        assert screen.query_one(Input).has_focus
        private_contents = private_path.read_bytes()
        public_key = read_public_key(public_path).public_key
        runpod_http.side_effect = [
            httpx.Response(200, json={"keys": [OTHER_SSH_PUBLIC_KEY]}),
            httpx.Response(200, json={"keys": [OTHER_SSH_PUBLIC_KEY, public_key]}),
        ]
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert private_path.read_bytes() == private_contents
        assert json.loads(runpod_http.call_args.args[0].content) == {"keys": [OTHER_SSH_PUBLIC_KEY, public_key]}
        assert b"PRIVATE KEY" not in runpod_http.call_args.args[0].content

    run_app(GallocApp(settings, config_path), scenario)


def test_generation_preserves_existing_private_key(settings, config_path, runpod_http, home):
    """Never replace an existing private key even when its public file is missing."""

    private_path = home / ".ssh" / "galloc_ed25519"
    private_path.parent.mkdir()
    private_path.write_text("existing key")
    runpod_http.side_effect = [httpx.Response(200, json=DATA_CENTER_RESPONSE)]

    async def scenario(pilot):
        """Choose generation, then return to key selection after the conflict."""

        await enter_credentials(pilot)
        await pilot.press("enter", "end", "enter")
        await settle(pilot)
        assert "already exist" in pilot.app.screen.query_one(Message).content
        assert pilot.app.screen.query_one(OptionList).has_focus

    run_app(GallocApp(settings, config_path), scenario)
    assert private_path.read_text() == "existing key"
    assert not config_path.exists()
    assert runpod_http.call_count == 1


def test_exit_during_registration_waits_for_setup(settings, config_path, runpod_http, home):
    """Do not leave a configuration-writing thread behind or submit a second registration on repeated Enter."""

    public_path = home / "custom.pub"
    public_path.write_text(SSH_PUBLIC_KEY)
    request_started = threading.Event()
    answer_allowed = threading.Event()

    def handler(request):
        """Hold the registration response until exit and repeat-submission keys have been pressed."""

        if request.url.path.endswith("catalog/datacenters"):
            return httpx.Response(200, json=DATA_CENTER_RESPONSE)
        if request.method == "PUT":
            request_started.set()
            assert answer_allowed.wait(timeout=5)
            return httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY]})
        return httpx.Response(200, json={"keys": []})

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Request exit while saving, let the registration finish, then reach the list normally."""

        await enter_credentials(pilot)
        await pilot.press("enter", "enter")
        pilot.app.screen.query_one(Input).value = str(public_path)
        await pilot.press("enter")
        try:
            assert await asyncio.to_thread(request_started.wait, 5)
            await pilot.press("ctrl+c", "escape", "ctrl+q", "enter")
            assert isinstance(pilot.app.screen, SetupScreen)
            assert pilot.app.screen.busy
            assert not config_path.exists()
        finally:
            answer_allowed.set()
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert config_path.exists()

    run_app(GallocApp(settings, config_path), scenario)
    assert sum(call.args[0].method == "PUT" for call in runpod_http.call_args_list) == 1
