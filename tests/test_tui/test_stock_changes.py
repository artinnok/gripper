import json

import httpx
import pytest
from textual.widgets import Input, OptionList

from galloc.core import storage
from galloc.tui.add import AddScreen
from galloc.tui.app import GallocApp
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize("stage", ["region", "validation", "creation"])
@pytest.mark.parametrize("mode", ["ttl", "budget"])
def test_unavailable_gpu_restarts_add_from_scratch(settings, config_path, runpod_http, pod_data, stage, mode):
    """Reload the first step on refusal and require fresh GPU, region, and limit choices."""

    config_path.touch()
    sold_out = False
    replacement = make_gpu_data(id="NVIDIA RTX A4000", name="RTX A4000", price={"secure": 0.25})

    def handler(request):
        """Remove the original GPU from stock at the selected point in the flow."""

        if request.url.path.endswith("catalog/gpus") and sold_out:
            return httpx.Response(200, json={"gpus": [make_gpu_data(availability="NONE", dataCenters=[]), replacement]})
        if request.method == "POST" and stage == "creation":
            return httpx.Response(400, json={"error": "No capacity available"})
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Trigger the refusal, inspect the reset, then finish a completely new selection."""

        nonlocal sold_out
        await pilot.press("a")
        await settle(pilot)
        if stage == "region":
            sold_out = True
        await pilot.press("enter")
        await settle(pilot)
        if stage != "region":
            await pilot.press("enter")
            if mode == "budget":
                await pilot.press("down")
            await pilot.press("enter", "2")
            if mode == "ttl":
                await pilot.press("h")
            sold_out = stage == "validation"
            await pilot.press("enter")
            await settle(pilot)

        screen = pilot.app.screen
        assert isinstance(screen, AddScreen)
        assert screen.step == "gpu"
        assert screen.selected_gpu_model is None
        assert screen.selected_region is None
        assert screen.query_one(Input).value == ""
        assert screen.query_one(Message).content
        assert not screen.query_one(OptionList).disabled
        assert len(pilot.app.screen_stack) == 3
        if sold_out:
            assert "RTX A4000" in screen.query_one(OptionList).get_option_at_index(0).prompt
        with storage.open_database(settings.database_path) as connection:
            assert storage.get_gpu_list(connection) == []

        runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)
        await pilot.press("r")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "enter")
        assert screen.query_one(Input).value == ""
        await pilot.press("1", "h", "enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GallocApp(settings, config_path), scenario)
    creation_request_list = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "POST"]
    assert len(creation_request_list) == (2 if stage == "creation" else 1)
    assert json.loads(creation_request_list[-1].content)["gpu"]["id"] == "NVIDIA H200"


def test_empty_catalog_after_refusal_waits_for_manual_refresh(settings, config_path, runpod_http, pod_data):
    """Stop at the empty first step when all stock disappears, without a retry loop."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json={"gpus": [make_gpu_data()]}),
        httpx.Response(200, json={"gpus": [make_gpu_data(availability="NONE", dataCenters=[])]}),
        httpx.Response(200, json={"gpus": []}),
    ]

    async def scenario(pilot):
        """Lose the model while choosing a region, then refresh when stock returns."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, AddScreen)
        assert screen.query_one(OptionList).option_count == 0
        assert "No GPUs" in screen.query_one(Message).content
        assert runpod_http.call_count == 3

        runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)
        await pilot.press("r")
        await settle(pilot)
        assert screen.query_one(OptionList).option_count == 1

    run_app(GallocApp(settings, config_path), scenario)
