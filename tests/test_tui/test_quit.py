import httpx
import pytest
from textual.widgets import Static

from galloc.core import storage
from galloc.tui.app import GallocApp
from tests.test_tui.conftest import run_app


@pytest.mark.parametrize("key", ["q", "ctrl+q", "ctrl+c"])
def test_quit_can_be_cancelled_then_confirmed_without_deleting_gpus(
    settings, config_path, runpod_http, gpu, pod_data, key
):
    """Keep the app and GPU intact until Enter explicitly confirms the exit."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]
    app = GallocApp(settings, config_path)

    async def scenario(pilot):
        """Cancel the right-column warning, then open it again and confirm."""

        screen = app.screen
        detail = screen.query_one("#info", Static).content
        await pilot.press(key)
        assert app.return_code is None
        assert screen.query_one("#info", Static).parent.id == "detail"
        assert "GPUs will not be deleted" in screen.query_one("#info", Static).content
        assert "confirm" in screen.query_one("#keys", Static).content
        assert "cancel" in screen.query_one("#keys", Static).content

        await pilot.press("a", "d", "e", "r", "ctrl+q")
        assert app.return_code is None
        assert screen.pending_action == "quit"

        await pilot.press("escape")
        assert app.return_code is None
        assert screen.query_one("#info", Static).content == detail

        await pilot.press(key, "enter")
        assert app.return_code == 0

    run_app(app, scenario)

    runpod_http.assert_called_once()
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [gpu]


@pytest.mark.parametrize("opening_key_list", [[], ["p"], ["d"]])
def test_second_q_confirms_the_exit(settings, config_path, runpod_http, gpu, pod_data, opening_key_list):
    """Exit with q pressed twice, from the list, from another screen, and over an open deletion question."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]
    app = GallocApp(settings, config_path)

    async def scenario(pilot):
        """Read the warning the first q opens, then answer it with the same key."""

        await pilot.press(*opening_key_list, "q")
        assert app.return_code is None
        assert "GPUs will not be deleted" in app.screen.query_one("#info", Static).content
        assert "[$foreground]q[/]" in app.screen.query_one("#keys", Static).content

        await pilot.press("q")
        assert app.return_code == 0

    run_app(app, scenario)

    runpod_http.assert_called_once()
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [gpu]
