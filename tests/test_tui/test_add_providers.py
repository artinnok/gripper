import asyncio
import threading

import httpx
from pydantic import SecretStr
from textual.widgets import DataTable, Input, OptionList, Static

from galloc.core import storage
from galloc.settings import Settings
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle


def test_catalog_lists_every_provider_and_creates_at_the_chosen_one(
    settings, config_path, runpod_http, vast_http, vast_offer_data, vast_instance_data, local_key
):
    """Compare prices across providers in one list and rent the cheaper offer from the second provider."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    runpod_http.side_effect = [httpx.Response(200, json={"gpus": [make_gpu_data()]})]

    def serve_vast(request):
        """Serve the marketplace, the rental request, and the state of the rented instance."""

        if request.url.path.endswith("bundles/"):
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        if request.url.path.endswith("ssh/"):
            return httpx.Response(200, json=[{"id": 1, "key": local_key.public_key}])
        if request.method == "PUT":
            assert request.url.path.endswith("asks/1234/")
            return httpx.Response(200, json={"success": True, "new_contract": 5678})
        return httpx.Response(200, json={"instances": vast_instance_data})

    vast_http.side_effect = serve_vast

    async def scenario(pilot):
        """Read both providers' rows by descending price, then create from the second row."""

        await pilot.press("a")
        await settle(pilot)
        screen = pilot.app.screen
        choice = screen.query_one("#choice", OptionList)
        columns = screen.query_one("#columns", Static).content
        expensive, cheap = (choice.get_option_at_index(index).prompt for index in range(2))
        assert columns.split()[-1] == "PROVIDER"
        assert expensive.startswith("H200") and "$5.29" in expensive
        assert cheap.startswith("RTX 4090") and "$0.30" in cheap
        assert expensive.index("$") == cheap.index("$") == columns.index("USD/HOUR")
        assert expensive.index("runpod") == cheap.index("vast") == columns.index("PROVIDER")

        await pilot.press("down", "enter")
        await settle(pilot)
        assert "vast · RTX 4090  $0.30/hour" in screen.query_one("#progress", Static).content
        await pilot.press("enter", "enter")
        screen.query_one("#limit", Input).value = "30m"
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1

    run_app(GallocApp(settings, config_path), scenario)

    runpod_http.assert_called_once()
    with storage.open_database(settings.database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert (gpu.provider, gpu.provider_instance_id) == ("vast", "5678")


def test_failed_provider_keeps_the_other_providers_offers(
    settings, config_path, runpod_http, vast_http, vast_offer_data
):
    """Show what can still be created and name the provider whose catalog could not be read."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    runpod_http.side_effect = [httpx.Response(500)]
    vast_http.side_effect = lambda request: httpx.Response(200, json={"offers": [vast_offer_data]})

    async def scenario(pilot):
        """Open the catalog while one provider is failing."""

        await pilot.press("a")
        await settle(pilot)
        choice = pilot.app.screen.query_one("#choice", OptionList)
        info = pilot.app.screen.query_one("#info", Static)
        message = pilot.app.screen.query_one(Message).content

        assert choice.option_count == 1
        assert choice.get_option_at_index(0).prompt.endswith("vast")
        assert not choice.disabled
        assert info.render_line(0).text.split() == ["runpod", "failed"]
        assert info.render_line(1).text.split() == ["vast", "ready"]
        assert "runpod" in message and "500" in message

    run_app(GallocApp(settings, config_path), scenario)


def test_each_provider_is_shown_as_soon_as_it_answers(settings, config_path, runpod_http, vast_http, vast_offer_data):
    """List the first catalog beside the provider still loading, then merge the rest under the user's cursor."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    request_started = threading.Event()
    answer_allowed = threading.Event()
    cheap_gpu_data = make_gpu_data(id="NVIDIA RTX A4000", name="RTX A4000", price={"secure": 0.2})
    runpod_http.side_effect = [httpx.Response(200, json={"gpus": [make_gpu_data(), cheap_gpu_data]})]

    def serve_vast(request):
        """Hold the marketplace answer until the first provider's rows have been read."""

        request_started.set()
        answer_allowed.wait(timeout=5)
        return httpx.Response(200, json={"offers": [vast_offer_data]})

    vast_http.side_effect = serve_vast

    async def scenario(pilot):
        """Move the cursor in the partial catalog, then let the second provider answer."""

        await pilot.press("a")
        assert await asyncio.to_thread(request_started.wait, 5)
        choice = pilot.app.screen.query_one("#choice", OptionList)
        info = pilot.app.screen.query_one("#info", Static)
        try:
            for attempt in range(100):
                if choice.option_count:
                    break
                await pilot.pause(0.05)
            assert [choice.get_option_at_index(index).prompt.split()[-1] for index in range(2)] == ["runpod", "runpod"]
            assert info.render_line(0).text.split() == ["runpod", "ready"]
            assert info.render_line(1).text.split()[0::2] == ["vast", "loading"]
            await pilot.press("down")
        finally:
            answer_allowed.set()
        await settle(pilot)

        assert choice.option_count == 3
        assert "RTX 4090" in choice.get_option_at_index(1).prompt
        assert (choice.highlighted, "RTX A4000" in choice.get_option_at_index(2).prompt) == (2, True)
        assert info.render_line(1).text.split() == ["vast", "ready"]

    run_app(GallocApp(settings, config_path), scenario)

    runpod_http.assert_called_once()


def test_catalog_without_any_api_key_points_to_providers(tmp_path, config_path, runpod_http, vast_http):
    """Ask for a provider API key instead of requesting a catalog without credentials."""

    config_path.touch()
    settings = Settings(database_path=tmp_path / "state.db")

    async def scenario(pilot):
        """Open the catalog with no provider configured."""

        await pilot.press("a")
        await settle(pilot)

        assert pilot.app.screen.query_one("#choice", OptionList).option_count == 0
        assert "No provider has an API key" in pilot.app.screen.query_one(Message).content

    run_app(GallocApp(settings, config_path), scenario)

    runpod_http.assert_not_called()
    vast_http.assert_not_called()
