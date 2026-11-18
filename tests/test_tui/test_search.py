import asyncio
import threading

import httpx
import pytest
from pydantic import SecretStr
from textual.widgets import Input, OptionList, Static

from galloc.tui.add import AddScreen
from galloc.tui.add_catalog import GPUCatalog
from galloc.tui.add_steps import AddStep
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


@pytest.fixture
def search_catalog(settings, config_path, runpod_http, vast_http, vast_offer_data, pod_data, local_key):
    """Serve both providers with overlapping model names and distinct prices and identifiers."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    gpu_data_list = [
        make_gpu_data(),
        make_gpu_data(id="RTX3090", name="RTX 3090", price={"secure": 0.5}),
    ]
    offer_list = [vast_offer_data, vast_offer_data | {"id": 9999, "gpu_name": "H200", "dph_base": 2.5}]

    def serve_runpod(request):
        """Answer reads only; search and navigation must not create a Pod."""

        assert request.method == "GET"
        if request.url.path.endswith("catalog/gpus"):
            return httpx.Response(200, json={"gpus": gpu_data_list})
        return serve_catalog(request, pod_data, local_key.public_key)

    def serve_vast(request):
        """Serve GPU and key lookups without accepting rental requests."""

        if request.url.path.endswith("ssh/"):
            assert request.method == "GET"
            return httpx.Response(200, json=[{"id": 1, "key": local_key.public_key}])
        assert request.method == "POST" and request.url.path.endswith("bundles/")
        return httpx.Response(200, json={"offers": offer_list})

    runpod_http.side_effect = serve_runpod
    vast_http.side_effect = serve_vast
    return gpu_data_list


@pytest.mark.parametrize(
    "query, expected_rows",
    [
        ("h200", [("H200", "runpod"), ("H200", "vast")]),
        ("VAST", [("H200", "vast"), ("RTX 4090", "vast")]),
        ("  H200  vAsT ", [("H200", "vast")]),
        ("rtx runpod", [("RTX 3090", "runpod")]),
        ("[missing]", []),
    ],
)
def test_search_filters_names_and_providers_without_requests(
    settings, config_path, runpod_http, vast_http, search_catalog, query, expected_rows
):
    """Search one or both fields as plain text, then reset without leaving the GPU picker."""

    async def scenario(pilot):
        """Type a query, inspect live results, and return to the full list with Escape."""

        await pilot.press("a")
        await settle(pilot)
        screen = pilot.app.screen
        prompt, choice = screen.query_one("#search", Input), screen.query_one("#choice", OptionList)
        request_count = runpod_http.call_count + vast_http.call_count
        assert not prompt.display
        await pilot.press("/", *query)
        assert prompt.has_focus and prompt.value == query
        assert choice.option_count == len(expected_rows)
        for index, (name, provider_name) in enumerate(expected_rows):
            row = choice.get_option_at_index(index).prompt
            assert row.startswith(name) and row.endswith(provider_name)
        assert bool(screen.query_one("#note", Static).content) == (not expected_rows)
        assert runpod_http.call_count + vast_http.call_count == request_count

        await pilot.press("enter")
        assert screen.step == AddStep.GPU and choice.has_focus
        await pilot.press("escape")
        assert screen.step == AddStep.GPU
        assert choice.option_count == 4 and not prompt.display
        assert screen.query_one("#note", Static).content == ""
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)


def test_queued_selection_keeps_the_offer_after_the_catalog_changes(settings, config_path, search_catalog):
    """Select the offer seen at Enter even if the catalog is reordered before the event is handled."""

    async def scenario(pilot):
        """Queue selection, then insert a higher-priced offer before Textual dispatches it."""

        await pilot.press("a")
        await settle(pilot)
        screen = pilot.app.screen
        catalog = screen.query_one(GPUCatalog)
        provider, model = catalog.gpu_model_list[0]
        screen.query_one("#choice", OptionList).action_select()
        catalog.add_gpu_model_list("vast", [model.model_copy(update={"id": "9999", "hourly_price": 20})])
        await settle(pilot)
        assert screen.step == AddStep.REGION
        assert (screen.selected_provider, screen.selected_gpu_model) == (provider, model)

    run_app(GallocApp(settings, config_path), scenario)


def test_arrows_leave_search_and_select_the_highlighted_gpu(settings, config_path, search_catalog):
    """Navigate results without Tab and return to the same query with slash."""

    async def scenario(pilot):
        """Move from the input in both directions, edit the query, and select the second result."""

        await pilot.press("a")
        await settle(pilot)
        screen = pilot.app.screen
        prompt, choice = screen.query_one("#search", Input), screen.query_one("#choice", OptionList)
        await pilot.press("/", *"h200", "down")
        assert choice.has_focus and choice.highlighted == 1
        await pilot.press("/")
        assert prompt.has_focus and prompt.value == "h200"
        await pilot.press("up")
        assert choice.has_focus and choice.highlighted == 0
        await pilot.press("/", "home", "right", "delete", "2")
        assert prompt.has_focus and prompt.value == "h200" and prompt.cursor_position == 2
        await pilot.press("down", "enter")
        await settle(pilot)
        assert screen.step == AddStep.REGION
        assert (screen.selected_provider, screen.selected_gpu_model.id) == ("vast", "9999")

    run_app(GallocApp(settings, config_path), scenario)


def test_arrows_on_empty_search_keep_the_picker_usable(settings, config_path, search_catalog):
    """An empty result list accepts navigation and allows editing the query again."""

    async def scenario(pilot):
        """Leave an empty search, try selection, and replace it with a matching query."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("/", *"missing", "down", "up", "enter")
        screen = pilot.app.screen
        assert screen.step == AddStep.GPU and screen.query_one("#choice", OptionList).option_count == 0
        await pilot.press("/", "ctrl+a", "ctrl+k", *"vast", "down")
        assert screen.query_one("#choice", OptionList).has_focus
        assert screen.query_one("#choice", OptionList).option_count == 2

    run_app(GallocApp(settings, config_path), scenario)


@pytest.mark.parametrize(
    "step, key_list, value, preview",
    [(AddStep.TTL, [], "1h", "about $2.50"), (AddStep.BUDGET, ["down"], "2.5", "Estimated TTL: 1h")],
)
def test_filtered_selection_uses_visible_offer_and_keeps_limit_input_separate(
    settings, config_path, search_catalog, step, key_list, value, preview
):
    """Select the filtered Vast row and keep search text and arrow handling out of the limit input."""

    async def scenario(pilot):
        """Search, select, enter either limit without losing focus, and return to the filtered catalog."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("/", *"h200 vast", "enter", "enter")
        await settle(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, AddScreen) and screen.step == AddStep.REGION
        assert (screen.selected_provider, screen.selected_gpu_model.id) == ("vast", "9999")
        assert not screen.query_one(GPUCatalog).display
        await pilot.press("enter", *key_list, "enter")
        limit_input = screen.query_one("#limit", Input)
        assert screen.step == step and limit_input.value == ""
        await pilot.press("up", "down", *value)
        assert limit_input.has_focus and limit_input.value == value
        assert preview in screen.query_one("#preview", Static).content
        await pilot.press("escape", "escape", "escape")
        assert screen.step == AddStep.GPU
        assert screen.query_one("#search", Input).value == "h200 vast"
        assert screen.query_one("#choice", OptionList).option_count == 1

    run_app(GallocApp(settings, config_path), scenario)


def test_closing_search_keeps_the_found_gpu_under_the_cursor(settings, config_path, search_catalog):
    """Select the model found by search after Esc restores the whole catalog, not the catalog's first row."""

    async def scenario(pilot):
        """Find the Vast H200, close search, and select from the full list."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("/", *"h200 vast", "enter", "escape")
        screen = pilot.app.screen
        assert screen.query_one("#choice", OptionList).option_count == 4
        await pilot.press("enter")
        await settle(pilot)
        assert (screen.selected_provider, screen.selected_gpu_model.id) == ("vast", "9999")

    run_app(GallocApp(settings, config_path), scenario)


def test_refresh_keeps_search_and_filtered_list_scrolls(settings, config_path, search_catalog):
    """Apply an existing query to refreshed results and keep the final result visible in a small terminal."""

    async def scenario(pilot):
        """Refresh into a long filtered list without moving the headers or the search field."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("/", *"h200 runpod", "enter")
        search_catalog[:] = [make_gpu_data(id=f"model-{index}", name=f"H200 {index:02}") for index in range(32)]
        await pilot.press("r")
        await settle(pilot)
        screen = pilot.app.screen
        prompt, choice = screen.query_one("#search", Input), screen.query_one("#choice", OptionList)
        assert prompt.value == "h200 runpod" and prompt.display
        assert choice.has_focus and choice.option_count == 32
        await pilot.resize_terminal(80, 16)
        header_region = screen.query_one("#columns").region
        await pilot.press("/", "down", "end")
        assert choice.has_focus and choice.highlighted == 31
        assert "H200 31" in choice.render_line(choice.content_size.height - 1).text
        assert prompt.region.bottom <= header_region.y
        assert screen.query_one("#columns").region == header_region
        assert choice.region.bottom <= screen.query_one("#keys").region.y
        assert screen.scroll_y == 0

    run_app(GallocApp(settings, config_path), scenario)


def test_late_catalog_answer_preserves_search_text_and_focus(
    settings, config_path, search_catalog, vast_http, vast_offer_data
):
    """Apply the current query to a slower provider without interrupting typing."""

    request_started = threading.Event()
    answer_allowed = threading.Event()

    def serve_vast(request):
        """Hold the second provider until the user is already searching the first provider's models."""

        request_started.set()
        answer_allowed.wait(timeout=5)
        return httpx.Response(200, json={"offers": [vast_offer_data | {"gpu_name": "H200"}]})

    vast_http.side_effect = serve_vast

    async def scenario(pilot):
        """Continue typing after the late answer and edit within the query without losing the cursor."""

        await pilot.press("a")
        assert await asyncio.to_thread(request_started.wait, 5)
        try:
            await pilot.press("/", "h", "2")
        finally:
            answer_allowed.set()
        await settle(pilot)
        prompt = pilot.app.screen.query_one("#search", Input)
        assert prompt.has_focus and prompt.value == "h2"
        await pilot.press("0", "0")
        assert prompt.value == "h200"
        assert pilot.app.screen.query_one("#choice", OptionList).option_count == 2
        await pilot.press("home", "delete", "h")
        assert prompt.value == "h200" and prompt.cursor_position == 1

    run_app(GallocApp(settings, config_path), scenario)
