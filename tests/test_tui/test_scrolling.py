import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import DataTable, Input, OptionList, Static

from galloc.core import storage
from galloc.core.schemas import GPU
from galloc.tui.app import GallocApp
from tests.conftest import SSH_PUBLIC_KEY, make_gpu_data
from tests.test_tui.conftest import run_app, settle


@pytest.fixture
def long_catalog(config_path, runpod_http, local_key):
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
        if request.url.path.endswith("account/ssh-keys"):
            return httpx.Response(200, json={"keys": [local_key.public_key]})
        if request.url.path.endswith("/catalog/gpus"):
            return httpx.Response(200, json={"gpus": gpu_list})
        assert request.url.path.endswith("/catalog/datacenters")
        return httpx.Response(200, json={"dataCenters": region_list})

    runpod_http.side_effect = handler


@pytest.mark.parametrize(
    "step, last_label, choice_id, columns_id",
    [("gpu", "GPU 31", "#choice", "#columns"), ("region", "REGION-31", "#step-choice", "#step-columns")],
)
@pytest.mark.parametrize("control", ["keyboard", "mouse"])
def test_long_picker_scrolls_within_the_screen(
    settings, config_path, long_catalog, step, last_label, choice_id, columns_id, control
):
    """Keep the last choice reachable and headers fixed, including after a terminal resize."""

    async def scenario(pilot):
        """Scroll both ways at normal and small sizes, then select the last visible row."""

        await pilot.press("a")
        await settle(pilot)
        if step == "region":
            await pilot.press("enter")
            await settle(pilot)

        screen = pilot.app.screen
        choice = screen.query_one(choice_id, OptionList)
        for width, height in ((120, 30), (80, 16)):
            await pilot.resize_terminal(width, height)
            await pilot.press("home")
            header_region = screen.query_one(columns_id).region
            await scroll_picker(pilot, choice, control, down=True)

            assert choice.scroll_y > 0
            assert screen.scroll_y == 0
            assert screen.query_one(columns_id).region == header_region
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


@pytest.fixture
def local_key_list(home):
    """Create a long list of distinct OpenSSH public keys in the isolated home."""

    key_directory = home / ".ssh"
    key_directory.mkdir()
    key_prefix = base64.b64decode(SSH_PUBLIC_KEY.split()[1])[:-32]
    public_key_list = []
    for index in range(32):
        key_data = base64.b64encode(key_prefix + bytes([index + 1]) * 32).decode()
        public_key = f"ssh-ed25519 {key_data} key-{index:02}"
        (key_directory / f"key-{index:02}.pub").write_text(public_key)
        public_key_list.append(public_key)
    return public_key_list


@pytest.mark.parametrize("entry", ["add", "setup"])
@pytest.mark.parametrize("control", ["keyboard", "mouse"])
def test_local_keys_scroll_to_the_final_action(settings, config_path, runpod_http, local_key_list, entry, control):
    """Keep the generate action reachable after many local keys, including during setup."""

    if entry == "add":
        config_path.touch()
    runpod_http.return_value = httpx.Response(200, json={"keys": [], "dataCenters": []})
    runpod_http.side_effect = None

    async def scenario(pilot):
        """Open key discovery and reach the final action and the last local key."""

        if entry == "add":
            await pilot.press("k")
            await settle(pilot)
            await pilot.press("a")
        else:
            await pilot.press("enter")
            pilot.app.screen.query_one(Input).value = "test-token"
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("enter")
        await settle(pilot)

        screen = pilot.app.screen
        choice = screen.query_one(OptionList)
        await check_list_scrolling(pilot, choice, "Generate new key", control)
        if entry == "add":
            await pilot.click(choice, offset=(1, choice.content_size.height - 1))
            assert screen.mode == "generate"
            assert screen.query_one(Input).has_focus
        else:
            await pilot.press("end", "up", "up")
            assert choice.highlighted == len(local_key_list) - 1
            assert "key-31.pub" in choice.render_line(choice.content_size.height - 3).text

    run_app(GallocApp(settings, config_path), scenario)
    assert all(call.args[0].method == "GET" for call in runpod_http.call_args_list)


@pytest.mark.parametrize("entity", ["gpu", "ssh_key"])
@pytest.mark.parametrize("control", ["keyboard", "mouse"])
def test_entity_tables_scroll_with_fixed_headers(
    settings, config_path, runpod_http, local_key_list, pod_data, entity, control
):
    """Keep GPU and registered-key tables reachable without moving their headers or detail pane."""

    config_path.touch()
    if entity == "gpu":
        with storage.open_database(settings.database_path) as connection:
            for index in range(32):
                storage.create_gpu(
                    connection,
                    GPU(
                        provider="runpod",
                        provider_instance_id=f"pod-{index}",
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    ),
                )

    def handler(request):
        """Serve live table rows without allowing changes to provider state."""

        assert request.method == "GET"
        if request.url.path.endswith("/account/ssh-keys"):
            return httpx.Response(200, json={"keys": local_key_list})
        return httpx.Response(200, json=pod_data | {"id": request.url.path.rsplit("/", 1)[-1]})

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Reach the final row, select it, and retain that selection through refresh."""

        if entity == "ssh_key":
            await pilot.press("k")
            await settle(pilot)

        screen = pilot.app.screen
        table = screen.query_one(DataTable)
        last_label = "gpu-32" if entity == "gpu" else "key-31"
        await check_list_scrolling(pilot, table, last_label, control)
        assert "NAME" in table.render_line(0).text

        await pilot.click(table, offset=(1, table.content_size.height - 1))
        assert table.cursor_row == 31
        assert last_label in screen.query_one("#info", Static).content
        await pilot.press("r")
        await settle(pilot)
        assert table.cursor_row == 31
        assert last_label in table.render_line(table.content_size.height - 1).text

    run_app(GallocApp(settings, config_path), scenario)


def test_provider_choice_fits_a_small_screen(settings, config_path):
    """Keep the initial provider choice visible and usable with the shared list layout."""

    async def scenario(pilot):
        """Select the provider in a short terminal and reach API key input."""

        await pilot.resize_terminal(80, 12)
        screen = pilot.app.screen
        choice = screen.query_one(OptionList)
        assert choice.region.bottom <= screen.content_region.bottom
        assert "runpod" in choice.render_line(0).text
        await pilot.press("enter")
        assert screen.query_one(Input).has_focus

    run_app(GallocApp(settings, config_path), scenario)


def test_limit_choices_fit_a_small_screen(settings, config_path, long_catalog):
    """Keep both TTL and budget visible after the catalog and region steps."""

    async def scenario(pilot):
        """Select a GPU and region, then choose budget in a short terminal."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter")
        await pilot.resize_terminal(80, 12)
        screen = pilot.app.screen
        choice = screen.query_one("#step-choice", OptionList)
        assert choice.region.bottom <= screen.query_one("#keys").region.y
        assert "TTL" in choice.render_line(0).text
        assert "Budget (USD)" in choice.render_line(1).text
        await pilot.press("end", "enter")
        assert screen.step == "budget"
        assert screen.query_one("#limit", Input).has_focus

    run_app(GallocApp(settings, config_path), scenario)


async def check_list_scrolling(pilot, widget: OptionList | DataTable, last_label: str, control: str) -> None:
    """Check both scroll directions and keep the viewport inside its parent after resizing."""

    screen = pilot.app.screen
    for width, height in ((120, 30), (80, 16)):
        await pilot.resize_terminal(width, height)
        await scroll_picker(pilot, widget, "keyboard", down=False)
        header_region = screen.query_one("#header").region
        await scroll_picker(pilot, widget, control, down=True)

        assert widget.scroll_y > 0
        assert screen.scroll_y == 0
        assert widget.parent.scroll_y == 0
        assert screen.query_one("#header").region == header_region
        assert widget.region.bottom <= widget.parent.content_region.bottom
        assert widget.region.bottom <= screen.content_region.bottom
        assert last_label in widget.render_line(widget.content_size.height - 1).text

        await scroll_picker(pilot, widget, control, down=False)
        assert widget.scroll_y == 0

    await scroll_picker(pilot, widget, control, down=True)


async def scroll_picker(pilot, choice: OptionList | DataTable, control: str, *, down: bool) -> None:
    """Reach a list edge through real keyboard or wheel events."""

    if control == "keyboard":
        key = "end" if down else "home"
        if isinstance(choice, DataTable):
            key = f"ctrl+{key}"
        await pilot.press(key)
        return

    event_type = MouseScrollDown if down else MouseScrollUp
    row_count = choice.row_count if isinstance(choice, DataTable) else choice.option_count
    for _ in range(row_count):
        choice.post_message(
            event_type(choice, x=1, y=1, delta_x=0, delta_y=0, button=0, shift=False, meta=False, ctrl=False)
        )
    await pilot.pause()
