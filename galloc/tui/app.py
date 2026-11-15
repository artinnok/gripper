import asyncio
import sqlite3
from pathlib import Path

from textual import work
from textual.app import App
from textual.content import Content
from textual.notifications import Notify
from textual.timer import Timer
from textual.worker import Worker, get_current_worker

from galloc.exceptions import GallocError
from galloc.settings import Settings
from galloc.tui.expiration import delete_expired_gpu_list
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.setup import SetupScreen
from galloc.tui.theme import AMBER_THEME, APP_CSS


TTL_CHECK_INTERVAL = 1


class GallocApp(App):
    """Run first-time setup when no configuration file exists, then show the GPU list."""

    TITLE = "galloc"
    CSS = APP_CSS
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = ""

    def __init__(self, settings: Settings, config_path: Path) -> None:
        """Receive loaded settings and the configuration file that setup writes."""

        super().__init__()
        self.settings = settings
        self.config_path = config_path
        self.gpu_screen: GPUListScreen | None = None
        self.expiration_timer: Timer | None = None
        self.expiration_worker: Worker | None = None
        self.register_theme(AMBER_THEME)
        self.theme = AMBER_THEME.name

    def on_mount(self) -> None:
        """Open the GPU list directly once a configuration file exists."""

        if self.config_path.exists():
            self.show_gpu_list(self.settings)
            return
        self.push_screen(SetupScreen(), self.show_gpu_list)

    def on_key(self) -> None:
        """Drop the current screen's message as soon as another key is pressed."""

        self.screen.query_one(Message).update("")

    async def action_quit(self) -> None:
        """Return to the GPU list and ask before leaving billable GPUs running."""

        if isinstance(self.screen, SetupScreen):
            self.exit()
            return
        while not isinstance(self.screen, GPUListScreen):
            await self.pop_screen()
        self.screen.action_quit()

    async def action_help_quit(self) -> None:
        """Use the same confirmation when Ctrl+C requests an exit."""

        await self.action_quit()

    def on_notify(self, event: Notify) -> None:
        """Route Textual notifications to the current screen's inline status."""

        event.prevent_default()
        notification = event.notification
        text = notification.message
        if notification.markup:
            text = Content.from_markup(text).plain
        if notification.title:
            text = f"{notification.title}: {text}"
        self.screen.query_one(Message).show(text, error=notification.severity == "error")

    def show_gpu_list(self, settings: Settings) -> None:
        """Open the list and start deadline checks for this session on every screen."""

        self.settings = settings
        self.gpu_screen = GPUListScreen()
        self.push_screen(self.gpu_screen)
        self.expiration_timer = self.set_interval(TTL_CHECK_INTERVAL, self.check_expiration)
        self.call_after_refresh(self.check_expiration)

    def check_expiration(self) -> None:
        """Start a deadline check only after the preceding pass has finished."""

        if self.expiration_worker is None or self.expiration_worker.is_finished:
            self.expiration_worker = self.expire_gpu_list()

    @work
    async def expire_gpu_list(self) -> None:
        """Delete overdue GPUs off the input loop and report the result inline."""

        try:
            deleted_name_list, error_list = await asyncio.to_thread(
                delete_expired_gpu_list, self.settings, get_current_worker().cancelled_event
            )
        except (GallocError, sqlite3.Error, OSError) as exception:
            self.screen.query_one(Message).show(f"TTL: {exception}", error=True)
            return

        if deleted_name_list:
            self.gpu_screen.refresh_gpu_list()
        if error_list:
            self.screen.query_one(Message).show("\n".join(error_list), error=True)
        elif deleted_name_list:
            self.screen.query_one(Message).show(f"TTL: deleted {', '.join(deleted_name_list)}")

    def confirm_quit(self) -> None:
        """Stop deadline checks; an already submitted deletion may finish before the process exits."""

        self.expiration_timer.stop()
        if self.expiration_worker is not None:
            self.expiration_worker.cancel()
        self.exit()
