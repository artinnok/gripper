import asyncio
from pathlib import Path

from pydantic import SecretStr
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static

from galloc.exceptions import GallocError
from galloc.providers.factory import PROVIDER_NAME_LIST
from galloc.settings import Settings
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.theme import CURRENT, DONE


GENERATE_KEY_OPTION = "Generate new key"
API_KEY_NOTE_MAP = {
    "runpod": "Pods read/write · Catalog read · Authentication read/write\nconsole.runpod.io/user/credentials",
}


class SetupScreen(Screen[Settings]):
    """Collect a provider, its API key, and an SSH key before the first GPU list."""

    BINDINGS = [Binding("escape", "app.quit", "Cancel")]

    def __init__(self) -> None:
        """Start at provider selection with nothing confirmed or saved."""

        super().__init__()
        self.step = "provider"
        self.provider_name = ""
        self.api_key = ""
        self.confirmed_settings: Settings | None = None
        self.public_key_path_list: list[Path] = []

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the one widget that takes the current answer."""

        yield Static("[b $primary]galloc setup[/]", id="header")
        yield Static(id="progress")
        yield OptionList(*PROVIDER_NAME_LIST, id="choice", compact=True)
        yield Input(id="api-key", password=True, compact=True)
        yield Static(id="note")
        yield Message()

    def on_mount(self) -> None:
        """Begin with the provider list as the only active widget."""

        self.query_one("#progress", Static).update(f"{CURRENT} Provider")
        self.query_one(Input).display = False
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Take the provider first; the same list later takes the SSH key choice."""

        if self.step == "provider":
            self._show_api_key_step(PROVIDER_NAME_LIST[event.option_index])
            return

        public_key_path = None
        if event.option_index < len(self.public_key_path_list):
            public_key_path = self.public_key_path_list[event.option_index]
        event.option_list.disabled = True
        self.finish_setup(public_key_path)

    def _show_api_key_step(self, provider_name: str) -> None:
        """Ask for the key beside the permissions it needs and where to create it."""

        self.step = "api_key"
        self.provider_name = provider_name
        self.query_one("#progress", Static).update(f"{DONE} Provider   {provider_name}\n{CURRENT} API key")
        self.query_one("#note", Static).update(API_KEY_NOTE_MAP[provider_name])
        self.query_one(OptionList).display = False

        api_key_input = self.query_one(Input)
        api_key_input.display = True
        api_key_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Check a non-empty key with the provider before moving on."""

        api_key = event.value.strip()
        if not api_key:
            return
        event.input.disabled = True
        self.check_api_key(api_key)

    @work
    async def check_api_key(self, api_key: str) -> None:
        """Verify the key off the input loop; a refused key stays editable."""

        settings = self.app.settings.model_copy(
            update={"provider": self.provider_name, f"{self.provider_name}_api_key": SecretStr(api_key)}
        )
        try:
            await asyncio.to_thread(operations.check_api_key, settings)
        except (GallocError, OSError) as exception:
            self._show_failure(self.query_one(Input), str(exception))
            return

        self._show_ssh_key_step(settings, api_key)

    def _show_ssh_key_step(self, settings: Settings, api_key: str) -> None:
        """Offer the public keys already in ~/.ssh and generation of a new pair."""

        self.step = "ssh_key"
        self.confirmed_settings = settings
        self.api_key = api_key
        self.public_key_path_list = sorted((Path.home() / ".ssh").glob("*.pub"))
        self.query_one("#progress", Static).update(
            f"{DONE} Provider   {self.provider_name}\n{DONE} API key    ••••{api_key[-4:]}\n{CURRENT} SSH key"
        )
        self.query_one("#note", Static).update("")
        self.query_one(Input).display = False

        choice = self.query_one(OptionList)
        choice.clear_options()
        choice.add_options([f"~/.ssh/{path.name}" for path in self.public_key_path_list])
        choice.add_option(GENERATE_KEY_OPTION)
        choice.highlighted = 0
        choice.display = True
        choice.focus()

    @work
    async def finish_setup(self, public_key_path: Path | None) -> None:
        """Register the key and save the configuration, then hand the settings to the app."""

        try:
            public_key_path = await asyncio.to_thread(
                operations.finish_setup, self.confirmed_settings, self.app.config_path, self.api_key, public_key_path
            )
        except (GallocError, OSError) as exception:
            self._show_failure(self.query_one(OptionList), str(exception))
            return

        self.dismiss(self.confirmed_settings.model_copy(update={"ssh_public_key": public_key_path}))

    def _show_failure(self, widget: Widget, message: str) -> None:
        """Report the failure and let the same step be answered again."""

        self.query_one(Message).show(message, error=True)
        widget.disabled = False
        widget.focus()
