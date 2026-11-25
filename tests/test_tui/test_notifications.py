import asyncio

from textual.widgets import Static

from gripper.tui.app import GripperApp
from gripper.tui.gpu_list import GPUListScreen
from gripper.tui.message import Message
from tests.test_tui.conftest import settle
from tests.test_tui.test_add import serve_catalog


def test_ctrl_c_confirms_exit_in_the_detail_column(settings, config_path, runpod_http, pod_data):
    """Use the detail column from both dashboard and add flow without showing a toast."""

    config_path.touch()
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)
    app = GripperApp(settings, config_path)

    async def scenario():
        """Press Ctrl+C on the dashboard and on the region step shown in the report."""

        async with app.run_test(size=(120, 30), notifications=True) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert "will remain billable" in app.screen.query_one("#info", Static).content
            assert app.screen.pending_action == "quit"
            assert not app.query("Toast")

            await pilot.press("escape", "a")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert isinstance(app.screen, GPUListScreen)
            assert "will remain billable" in app.screen.query_one("#info", Static).content
            assert app.screen.pending_action == "quit"
            assert not app.query("Toast")

            await pilot.press("escape")
            assert app.screen.pending_action is None
            assert "No GPUs." in app.screen.query_one("#info", Static).content

    asyncio.run(scenario())


def test_notifications_use_inline_status_during_setup(settings, config_path):
    """Preserve notification text, titles, and errors without a popup during first launch."""

    app = GripperApp(settings, config_path)

    async def scenario():
        """Send formatted and literal notifications through the application event handler."""

        async with app.run_test(size=(120, 30), notifications=True) as pilot:
            await settle(pilot)
            app.notify("Request [b]failed[/b]", title="Provider", severity="error")
            await pilot.pause()
            assert app.screen.query_one(Message).content == "[$error]Provider: Request failed[/]"
            assert not app.query("Toast")

            app.notify("Use [name]", markup=False)
            await pilot.pause()
            assert app.screen.query_one(Message).content == "[$success]Use \\[name][/]"
            assert not app.query("Toast")

    asyncio.run(scenario())
