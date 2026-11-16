import httpx
import pytest
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import OptionList

from galloc.tui.app import GallocApp
from tests.conftest import make_gpu_data
from tests.test_tui.conftest import run_app, settle


@pytest.fixture
def long_catalog(config_path, runpod_http):
    """Serve more GPU models and regions than fit on the terminal screen."""

    config_path.touch()
    region_list = [{"id": f"REGION-{index:02}", "name": f"Region {index}", "region": "EUROPE"} for index in range(32)]
    gpu_list = [
        make_gpu_data(
            id=f"gpu-model-{index}",
            name=f"GPU {index:02}",
            dataCenters=[{"id": region["id"], "availability": "HIGH"} for region in region_list],
        )
        for index in range(32)
    ]

    def handler(request):
        """Allow catalog reads only; scrolling must never create a GPU."""

        assert request.method == "GET"
        if request.url.path.endswith("/catalog/gpus"):
            return httpx.Response(200, json={"gpus": gpu_list})
        assert request.url.path.endswith("/catalog/datacenters")
        return httpx.Response(200, json={"dataCenters": region_list})

    runpod_http.side_effect = handler


@pytest.mark.parametrize("step, last_label", [("gpu", "GPU 31"), ("region", "REGION-31")])
@pytest.mark.parametrize("control", ["keyboard", "mouse"])
def test_long_picker_scrolls_within_the_screen(settings, config_path, long_catalog, step, last_label, control):
    """Keep the last choice reachable and headers fixed, including after a terminal resize."""

    async def scenario(pilot):
        """Scroll both ways at normal and small sizes, then select the last visible row."""

        await pilot.press("a")
        await settle(pilot)
        if step == "region":
            await pilot.press("enter")
            await settle(pilot)

        screen = pilot.app.screen
        choice = screen.query_one(OptionList)
        for height in (30, 16):
            await pilot.resize_terminal(120, height)
            await pilot.press("home")
            header_region = screen.query_one("#columns").region
            await scroll_picker(pilot, choice, control, down=True)

            assert choice.scroll_y > 0
            assert screen.scroll_y == 0
            assert screen.query_one("#columns").region == header_region
            assert choice.region.bottom <= screen.query_one("#keys").region.y
            assert last_label in choice.render_line(choice.content_size.height - 1).text

            await scroll_picker(pilot, choice, control, down=False)
            assert choice.scroll_y == 0

        await scroll_picker(pilot, choice, control, down=True)
        await pilot.click(choice, offset=(1, choice.content_size.height - 1))
        await settle(pilot)
        if step == "gpu":
            assert screen.selected_gpu_model.name == last_label
        else:
            assert screen.selected_region.id == last_label

    run_app(GallocApp(settings, config_path), scenario)


async def scroll_picker(pilot, choice: OptionList, control: str, *, down: bool) -> None:
    """Reach a list edge through real keyboard or wheel events."""

    if control == "keyboard":
        await pilot.press("end" if down else "home")
        return

    event_type = MouseScrollDown if down else MouseScrollUp
    for _ in range(choice.option_count):
        choice.post_message(
            event_type(choice, x=1, y=1, delta_x=0, delta_y=0, button=0, shift=False, meta=False, ctrl=False)
        )
    await pilot.pause()
