import json

import httpx
import pytest
from textual.widgets import Input, OptionList, Static

from galloc.core.ssh_keys import generate_key_pair, read_public_key
from galloc.settings import load_settings
from galloc.tui.add import AddScreen
from galloc.tui.add_steps import AddStep
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.ssh_key.add import SSHKeyAddScreen
from tests.conftest import SSH_PUBLIC_KEY, make_gpu_data
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize("source", ["local", "custom", "generate"])
def test_ssh_setup_precedes_vast_rental_and_remembers_the_identity(
    config_path, write_config, home, runpod_http, vast_http, vast_offer_data, vast_instance_data, source, ssh_session
):
    """Register with the chosen provider, then rent once and keep the selected identity after restarting."""

    write_config(defaults={"provider": "runpod"})
    with config_path.open("a") as config_file:
        config_file.write('\n[providers.vast]\napi_key = "vast-token"\n')
    settings = load_settings(config_path)
    private_path = home / ".ssh" / "galloc_ed25519"
    if source == "custom":
        private_path = home / "custom keys" / "vast identity"
    expected_ssh_arguments = ["ssh", "-i", str(private_path), "root@ssh1.vast.ai", "-p", "12345"]
    public_path = private_path.with_name(f"{private_path.name}.pub")
    if source != "generate":
        generate_key_pair(private_path)
    registered_key_list = []
    runpod_http.side_effect = lambda request: httpx.Response(200, json={"gpus": [make_gpu_data()]})

    def serve_vast(request):
        """Require a registered public key before accepting any billable instance request."""

        if request.url.path.endswith("bundles/"):
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        if request.url.path.endswith("ssh/"):
            if request.method == "POST":
                public_key = json.loads(request.content)["ssh_key"]
                assert public_key == read_public_key(public_path).public_key
                registered_key_list.append({"id": 1, "key": public_key})
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json=registered_key_list)
        if request.method == "PUT":
            assert registered_key_list
            assert request.url.path.endswith("asks/1234/")
            return httpx.Response(200, json={"success": True, "new_contract": 5678})
        assert request.method == "GET" and request.url.path.endswith("instances/5678/")
        return httpx.Response(200, json={"instances": vast_instance_data})

    vast_http.side_effect = serve_vast

    async def scenario(pilot):
        """Choose Vast, set up access, then finish the existing region and TTL steps."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("down", "enter")
        await settle(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, SSHKeyAddScreen)
        assert "vast" in screen.query_one("#progress", Static).content
        await pilot.resize_terminal(80, 16)
        choice = screen.query_one(OptionList)
        assert choice.region.right <= screen.query_one("#detail").region.x
        assert choice.region.bottom <= screen.query_one("#keys").region.y
        await pilot.press("end")
        assert "Generate new key" in choice.render_line(choice.option_count - 1).text
        await pilot.press("home")
        assert not any(call.args[0].method == "PUT" for call in vast_http.call_args_list)
        if source == "custom":
            await pilot.press("enter")
            screen.query_one(Input).value = str(public_path)
        elif source == "generate":
            await pilot.press("end", "enter")
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.REGION
        assert pilot.app.settings.provider == "runpod"
        assert pilot.app.settings.vast_ssh_public_key == public_path
        await pilot.press("enter", "enter", "3", "0", "m", "enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        await pilot.press("s")
        ssh_session.assert_awaited_once_with(expected_ssh_arguments)
        assert "-i " in pilot.app.screen.query_one("#info", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    saved = load_settings(config_path)
    assert saved.vast_ssh_public_key == public_path
    assert saved.runpod_ssh_public_key is None
    assert len(registered_key_list) == 1
    assert sum(call.args[0].method == "PUT" for call in vast_http.call_args_list) == 1
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)

    ssh_session.reset_mock()

    async def restart(pilot):
        """Keep the usable SSH command and skip key selection when adding another GPU at the same provider."""

        await pilot.press("s")
        ssh_session.assert_awaited_once_with(expected_ssh_arguments)
        await pilot.press("a")
        await settle(pilot)
        await pilot.press("down", "enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, AddScreen)
        assert pilot.app.screen.step == AddStep.REGION

    run_app(GallocApp(saved, config_path), restart)
    assert len(registered_key_list) == 1
    assert sum(call.args[0].method == "PUT" for call in vast_http.call_args_list) == 1


@pytest.mark.parametrize("key_state", ["matching", "public_only", "foreign"])
def test_skipping_ssh_requires_a_registered_local_pair(
    settings, config_path, home, runpod_http, pod_data, local_key, key_state
):
    """Compare fingerprints rather than comments, and never mistake an account key for local access."""

    config_path.touch()
    public_key = local_key.public_key.rsplit(" ", 1)[0] + " another-comment"
    if key_state == "public_only":
        (home / ".ssh" / "id_ed25519").unlink()
    elif key_state == "foreign":
        public_key = SSH_PUBLIC_KEY
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data, public_key)

    async def scenario(pilot):
        """Choose a GPU and either continue directly or offer key setup before any creation."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        if key_state == "matching":
            assert isinstance(pilot.app.screen, AddScreen)
            assert pilot.app.screen.step == AddStep.REGION
        else:
            assert isinstance(pilot.app.screen, SSHKeyAddScreen)
            if key_state == "public_only":
                await pilot.press("enter")
                await settle(pilot)
                assert "Private key not found" in pilot.app.screen.query_one(Message).content
            await pilot.press("escape")
            assert isinstance(pilot.app.screen, AddScreen)
            assert pilot.app.screen.step == AddStep.GPU
            assert not pilot.app.screen.query_one("#choice", OptionList).disabled

    run_app(GallocApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
