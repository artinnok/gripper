import json
import stat
import tomllib

import httpx
from textual.widgets import Input

from galloc.core.ssh_keys import read_public_key
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.setup import SetupScreen
from tests.conftest import SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle


DATA_CENTER_RESPONSE = {"dataCenters": [{"id": "US-KS-2", "name": "US-KS-2", "region": "NORTH_AMERICA"}]}


def test_first_launch_registers_an_existing_key_and_saves_configuration(settings, config_path, runpod_http, home):
    """Walk through provider, API key, and SSH key, then open the GPU list."""

    public_key_path = home / ".ssh" / "id_ed25519.pub"
    public_key_path.parent.mkdir()
    public_key_path.write_text(SSH_PUBLIC_KEY + "\n")
    runpod_http.side_effect = [
        httpx.Response(200, json=DATA_CENTER_RESPONSE),
        httpx.Response(200, json={"keys": []}),
        httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY]}),
    ]

    async def scenario(pilot):
        """Accept the only provider, enter a key, and choose the first public key."""

        await pilot.press("enter")
        pilot.app.screen.query_one(Input).value = "entered-token"
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.settings.runpod_api_key.get_secret_value() == "entered-token"

    run_app(GallocApp(settings, config_path), scenario)

    check_call, read_call, write_call = runpod_http.call_args_list
    assert check_call.args[0].headers["Authorization"] == "Bearer entered-token"
    assert json.loads(write_call.args[0].content) == {"keys": [SSH_PUBLIC_KEY]}
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert tomllib.loads(config_path.read_text()) == {
        "defaults": {"provider": "runpod", "ssh_public_key": str(public_key_path)},
        "providers": {"runpod": {"api_key": "entered-token"}},
    }


def test_first_launch_can_generate_a_new_key(settings, config_path, runpod_http, home):
    """Create a key pair when the home directory has no public key to choose."""

    registered_key_list = []

    def handler(request):
        """Accept the catalog check, then record the registered key list."""

        if request.url.path.endswith("catalog/datacenters"):
            return httpx.Response(200, json=DATA_CENTER_RESPONSE)
        if request.method == "PUT":
            registered_key_list.extend(json.loads(request.content)["keys"])
        return httpx.Response(200, json={"keys": registered_key_list})

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Enter a key and take the only SSH option, which generates a pair."""

        await pilot.press("enter")
        pilot.app.screen.query_one(Input).value = "entered-token"
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)

    generated_key = read_public_key(home / ".ssh" / "galloc_ed25519.pub")
    assert registered_key_list == [generated_key.public_key]
    assert (home / ".ssh" / "galloc_ed25519").exists()


def test_refused_api_key_keeps_setup_open_and_saves_nothing(settings, config_path, runpod_http):
    """Stay on the API key step when the provider rejects the entered key."""

    runpod_http.side_effect = [httpx.Response(401)]

    async def scenario(pilot):
        """Enter a key the provider refuses."""

        await pilot.press("enter")
        pilot.app.screen.query_one(Input).value = "wrong-token"
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, SetupScreen)
        assert pilot.app.screen.step == "api_key"
        assert "401" in pilot.app.screen.query_one(Message).content
        assert not pilot.app.screen.query_one(Input).disabled

    run_app(GallocApp(settings, config_path), scenario)

    assert not config_path.exists()


def test_cancelled_setup_saves_nothing(settings, config_path, runpod_http):
    """Leave no configuration behind when setup is cancelled."""

    async def scenario(pilot):
        """Cancel on the first step."""

        await pilot.press("escape")

    run_app(GallocApp(settings, config_path), scenario)

    assert not config_path.exists()
    runpod_http.assert_not_called()
