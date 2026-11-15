from pathlib import Path

from textual.app import App
from textual.content import Content
from textual.notifications import Notify

from galloc.settings import Settings
from galloc.tui.gpu_list import GPUListScreen
from galloc.tui.message import Message
from galloc.tui.setup import SetupScreen
from galloc.tui.theme import AMBER_THEME, APP_CSS


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
        self.register_theme(AMBER_THEME)
        self.theme = AMBER_THEME.name

    def on_mount(self) -> None:
        """Open the GPU list directly once a configuration file exists."""

        if self.config_path.exists():
            self.push_screen(GPUListScreen())
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
        """Use the settings confirmed during setup for the rest of the session."""

        self.settings = settings
        self.push_screen(GPUListScreen())

    def confirm_quit(self) -> None:
        """Leave billable GPUs running after the user confirms the exit."""

        self.exit()
