import httpx
from pydantic import SecretStr
from textual.widgets import DataTable, Input, Static

from galloc.settings import load_settings
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.provider.list import ProviderListScreen
from tests.test_tui.conftest import run_app, settle


SAVED_CONFIG = '# my notes\n[defaults]\nprovider = "runpod"\n\n[providers.runpod]\napi_key = "test-token"\n'


def test_api_key_is_checked_saved_and_used_without_restart(settings, config_path, vast_http, runpod_http):
    """Add the second provider's key from the interface, keeping the rest of the file untouched."""

    config_path.write_text(SAVED_CONFIG)
    vast_http.side_effect = [httpx.Response(200, json=[])]

    async def scenario(pilot):
        """Open providers, enter the Vast.ai key with masked input, and see it become configured."""

        await pilot.press("p")
        screen = pilot.app.screen
        assert isinstance(screen, ProviderListScreen)
        table = screen.query_one(DataTable)
        assert table.get_row_at(0) == ["runpod", "configured", "yes"]
        assert table.get_row_at(1) == ["vast", "not configured", ""]

        await pilot.press("down", "a")
        prompt = screen.query_one(Input)
        assert prompt.has_focus and prompt.password
        assert "cloud.vast.ai" in screen.query_one("#info", Static).content
        prompt.value = "entered-vast-key"
        await pilot.press("tab", "up")
        assert table.cursor_row == 1
        assert prompt.has_focus
        await pilot.press("enter")
        await settle(pilot)

        assert table.get_row_at(1) == ["vast", "configured", ""]
        assert not prompt.display
        assert "Saved" in screen.query_one(Message).content
        assert pilot.app.settings.vast_api_key.get_secret_value() == "entered-vast-key"
        assert pilot.app.settings.provider == "runpod"

    run_app(GallocApp(settings, config_path), scenario)

    (check_call,) = vast_http.call_args_list
    assert check_call.args[0].headers["Authorization"] == "Bearer entered-vast-key"
    runpod_http.assert_not_called()
    assert config_path.read_text().startswith(SAVED_CONFIG)
    saved_settings = load_settings(config_path)
    assert saved_settings.vast_api_key.get_secret_value() == "entered-vast-key"
    assert saved_settings.runpod_api_key.get_secret_value() == "test-token"


def test_refused_api_key_stays_editable_and_saves_nothing(settings, config_path, vast_http):
    """Keep the prompt open after the provider rejects the key, then cancel without changes."""

    config_path.write_text(SAVED_CONFIG)
    vast_http.side_effect = [httpx.Response(401, json={"success": False, "error": "auth_error", "msg": "Invalid"})]

    async def scenario(pilot):
        """Enter a key the provider refuses, then leave the prompt and the screen."""

        await pilot.press("p", "down", "a")
        screen = pilot.app.screen
        prompt = screen.query_one(Input)
        prompt.value = "wrong-vast-key"
        await pilot.press("enter")
        await settle(pilot)

        assert screen.query_one(Message).content.startswith("[$error]")
        assert prompt.has_focus and not prompt.disabled
        assert pilot.app.settings.vast_api_key is None

        await pilot.press("escape")
        assert not prompt.display
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)

    assert config_path.read_text() == SAVED_CONFIG


def test_default_provider_needs_a_key_and_is_saved(settings, config_path, runpod_http, vast_http):
    """Refuse an unconfigured default, then switch to a configured provider for new GPUs and SSH keys."""

    config_path.write_text(SAVED_CONFIG)

    async def scenario(pilot):
        """Try the provider without a key, give it one, and make it the default."""

        await pilot.press("p", "down", "u")
        screen = pilot.app.screen
        assert "Add an API key for vast first." in screen.query_one(Message).content
        assert pilot.app.settings.provider == "runpod"

        pilot.app.settings.vast_api_key = SecretStr("vast-token")
        await pilot.press("u")
        assert pilot.app.settings.provider == "vast"
        assert screen.query_one(DataTable).get_row_at(1) == ["vast", "configured", "yes"]

        await pilot.press("escape")
        assert "vast" in pilot.app.screen.query_one("#header", Static).content

    run_app(GallocApp(settings, config_path), scenario)

    assert load_settings(config_path).provider == "vast"
    assert config_path.read_text().startswith("# my notes\n")
    runpod_http.assert_not_called()
    vast_http.assert_not_called()
