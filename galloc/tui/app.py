from pathlib import Path

from textual.app import App

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

    def show_gpu_list(self, settings: Settings) -> None:
        """Use the settings confirmed during setup for the rest of the session."""

        self.settings = settings
        self.push_screen(GPUListScreen())
