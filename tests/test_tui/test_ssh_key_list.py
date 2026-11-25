import asyncio
from threading import Event

import httpx
import pytest
from pydantic import SecretStr
from textual.events import Key
from textual.widgets import DataTable, Static

from gripper.tui.app import GripperApp
from gripper.tui.gpu_list import GPUListScreen
from gripper.tui.message import Message
from gripper.tui.ssh_key.list import SSHKeyListScreen
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_FINGERPRINT, SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle


def test_key_list_has_selected_details_and_refreshes_from_provider(settings, config_path, registered_key_list):
    """Use a dedicated entity list and follow provider-side removals on refresh."""

    config_path.touch()
    registered_key_list.extend([SSH_PUBLIC_KEY, OTHER_SSH_PUBLIC_KEY])

    async def scenario(pilot):
        """Select two keys, refresh after external deletion, and return to GPUs."""

        await pilot.press("k")
        await settle(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, SSHKeyListScreen)
        table = screen.query_one(DataTable)
        detail = screen.query_one("#detail")
        assert table.region.right <= detail.region.x
        assert table.get_row_at(0) == ["laptop", "runpod", SSH_FINGERPRINT]
        assert SSH_PUBLIC_KEY in screen.query_one("#info", Static).content
        await pilot.press("down")
        assert OTHER_SSH_PUBLIC_KEY in screen.query_one("#info", Static).content

        registered_key_list.pop()
        await pilot.press("r")
        await settle(pilot)
        assert table.row_count == 1
        assert SSH_PUBLIC_KEY in screen.query_one("#info", Static).content
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, GPUListScreen)

    run_app(GripperApp(settings, config_path), scenario)


def test_key_removal_requires_confirmation_and_keeps_local_files(settings, config_path, registered_key_list, tmp_path):
    """Remove one provider registration without deleting another key or its local file."""

    config_path.touch()
    registered_key_list.extend([SSH_PUBLIC_KEY, OTHER_SSH_PUBLIC_KEY])
    key_path = tmp_path / "id_ed25519.pub"
    key_path.write_text(SSH_PUBLIC_KEY)

    async def scenario(pilot):
        """Cancel first, then confirm removal from the selected key's detail column."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("d")
        assert "Remove from runpod?" in pilot.app.screen.query_one("#info", Static).content
        await pilot.press("escape")
        assert len(registered_key_list) == 2
        await pilot.press("d", "enter")
        await settle(pilot)
        assert pilot.app.screen.query_one(DataTable).row_count == 1

    run_app(GripperApp(settings, config_path), scenario)
    assert registered_key_list == [OTHER_SSH_PUBLIC_KEY]
    assert key_path.read_text() == SSH_PUBLIC_KEY


def test_keys_of_every_provider_are_listed_and_removed_from_their_own(
    settings, config_path, registered_key_list, vast_http
):
    """Show the same key once per provider that has it and remove only the selected provider's registration."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    registered_key_list.append(SSH_PUBLIC_KEY)
    vast_key_list = [{"id": 22, "key": SSH_PUBLIC_KEY}]

    def serve_vast(request):
        """Read account keys and remove one of them by its provider ID."""

        if request.method == "DELETE":
            assert request.url.path.endswith("ssh/22/")
            vast_key_list.clear()
            return httpx.Response(200, json={"success": True})
        assert request.url.path.endswith("ssh/")
        return httpx.Response(200, json=vast_key_list)

    vast_http.side_effect = serve_vast

    async def scenario(pilot):
        """Read both rows, then remove the second provider's one."""

        await pilot.press("k")
        await settle(pilot)
        screen = pilot.app.screen
        table = screen.query_one(DataTable)
        assert table.get_row_at(0) == ["laptop", "runpod", SSH_FINGERPRINT]
        assert table.get_row_at(1) == ["laptop", "vast", SSH_FINGERPRINT]

        await pilot.press("down", "d")
        assert "Remove from vast?" in screen.query_one("#info", Static).content
        await pilot.press("enter")
        await settle(pilot)
        assert table.row_count == 1
        assert table.get_row_at(0) == ["laptop", "runpod", SSH_FINGERPRINT]

    run_app(GripperApp(settings, config_path), scenario)
    assert registered_key_list == [SSH_PUBLIC_KEY]


def test_failed_provider_keeps_the_other_providers_keys(settings, config_path, registered_key_list, vast_http):
    """List what could be read and name the provider whose keys could not."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    registered_key_list.append(SSH_PUBLIC_KEY)
    vast_http.side_effect = [httpx.Response(500)]

    async def scenario(pilot):
        """Open the key list while one provider is failing."""

        await pilot.press("k")
        await settle(pilot)
        screen = pilot.app.screen
        message = screen.query_one(Message).content

        assert screen.query_one(DataTable).get_row_at(0) == ["laptop", "runpod", SSH_FINGERPRINT]
        assert SSH_PUBLIC_KEY in screen.query_one("#info", Static).content
        assert "vast" in message and "500" in message

    run_app(GripperApp(settings, config_path), scenario)


def test_failed_key_refresh_clears_stale_rows_and_can_retry(settings, config_path, runpod_http):
    """Distinguish failed provider access from an empty key list and allow a fresh read."""

    config_path.touch()
    runpod_http.side_effect = [
        httpx.Response(200, json={"keys": [SSH_PUBLIC_KEY]}),
        httpx.Response(403),
        httpx.Response(200, json={"keys": []}),
    ]

    async def scenario(pilot):
        """Observe the access failure and restore the provider list."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("r")
        await settle(pilot)
        screen = pilot.app.screen
        assert screen.query_one(DataTable).row_count == 0
        assert "Provider data unavailable" in screen.query_one("#info", Static).content
        assert "403" in screen.query_one(Message).content
        await pilot.press("r")
        await settle(pilot)
        assert "No SSH keys registered" in screen.query_one("#info", Static).content

    run_app(GripperApp(settings, config_path), scenario)


@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+q"])
def test_quit_from_nested_key_screen_uses_gpu_exit_confirmation(settings, config_path, registered_key_list, key):
    """Return from nested SSH views to the existing exit warning without a popup."""

    config_path.touch()

    async def scenario(pilot):
        """Open local key selection, request exit, and cancel the warning."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("a", key)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert "GPUs will not be deleted" in pilot.app.screen.query_one("#info", Static).content
        await pilot.press("escape")
        assert pilot.app.return_code is None

    run_app(GripperApp(settings, config_path), scenario)


def test_queued_enter_events_remove_key_once_and_keep_list_open(
    settings, config_path, registered_key_list, runpod_http
):
    """Handle a burst of confirmation keys with one provider write and one screen update."""

    config_path.touch()
    registered_key_list.extend([SSH_PUBLIC_KEY, OTHER_SSH_PUBLIC_KEY])
    write_started, allow_write = Event(), Event()
    key_handler = runpod_http.side_effect

    def delayed_write(request):
        """Keep the provider write in flight while queued key events reach their workers."""

        if request.method == "PUT":
            write_started.set()
            assert allow_write.wait(5)
        return key_handler(request)

    runpod_http.side_effect = delayed_write

    async def scenario(pilot):
        """Queue Enter without yielding, then complete deletion and inspect the same screen."""

        await pilot.press("k")
        await settle(pilot)
        screen = pilot.app.screen
        await pilot.press("d")
        try:
            for _ in range(3):
                pilot.app.post_message(Key("enter", "\r"))
            assert await asyncio.to_thread(write_started.wait, 5)
            await pilot.pause()
        finally:
            allow_write.set()
        await settle(pilot)

        assert sum(call.args[0].method == "PUT" for call in runpod_http.call_args_list) == 1
        assert pilot.app.screen is screen
        assert screen.query_one(DataTable).row_count == 1
        assert OTHER_SSH_PUBLIC_KEY in screen.query_one("#info", Static).content

    run_app(GripperApp(settings, config_path), scenario)
    assert registered_key_list == [OTHER_SSH_PUBLIC_KEY]
