import asyncio
import threading

import pytest
from textual.widgets import Input, Static

from galloc.tui.app import GallocApp
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize("step", ["gpu", "region"])
def test_typing_during_catalog_loading_does_not_enter_the_limit(
    settings, config_path, runpod_http, pod_data, step, local_key
):
    """Keep letters away from hidden inputs while catalog requests are in flight."""

    config_path.touch()
    request_started = threading.Event()
    answer_allowed = threading.Event()
    catalog_reads = 0

    def handler(request):
        """Hold the initial catalog or the lookup of regions for the selected GPU."""

        nonlocal catalog_reads
        if request.url.path.endswith("catalog/gpus"):
            catalog_reads += 1
            if catalog_reads == (1 if step == "gpu" else 2):
                request_started.set()
                answer_allowed.wait(timeout=5)
        return serve_catalog(request, pod_data, local_key.public_key)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Type while loading, then enter a TTL normally once the choices are ready."""

        await pilot.press("a")
        if step == "region":
            await settle(pilot)
            await pilot.press("enter")
        assert await asyncio.to_thread(request_started.wait, 5)
        try:
            await pilot.press("d", "f", "j", "k", "a", "r", "x", "tab", "enter")
            assert pilot.app.screen.query_one("#limit", Input).value == ""
        finally:
            answer_allowed.set()
        await settle(pilot)
        if step == "gpu":
            await pilot.press("enter")
            await settle(pilot)
        await pilot.press("enter", "enter", "1", "h")
        assert "about $5.29" in pilot.app.screen.query_one("#preview", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)
