import asyncio
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr, ValidationError
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from galloc.core.ssh_keys import generate_key_pair
from galloc.exceptions import GallocError
from galloc.providers.factory import PROVIDER_NAME_LIST
from galloc.settings import Settings
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.theme import CURRENT, DONE


API_KEY_NOTE_MAP = {
    "runpod": "Pods read/write · Catalog read · Authentication read/write\nconsole.runpod.io/user/credentials",
    "vast": "instance_read · instance_write · user_read · user_write · misc\ncloud.vast.ai/manage-keys/",
}


class SetupStep(StrEnum):
    """Name the steps of the first-run configuration flow."""

    PROVIDER = "provider"
    API_KEY = "api_key"
    DISK = "disk"
    SSH_KEY = "ssh_key"
    KEY_PATH = "key_path"


class SetupScreen(Screen[Settings]):
    """Collect provider credentials, disk size, and an SSH key before the first GPU list."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self) -> None:
        """Start at provider selection with nothing confirmed or saved."""

        super().__init__()
        self.step: SetupStep = SetupStep.PROVIDER
        self.provider_name = ""
        self.api_key = ""
        self.confirmed_settings: Settings | None = None
        self.public_key_path_list: list[Path] = []
        self.busy = False

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the one widget that takes the current answer."""

        yield Static("[b $primary]galloc setup[/]", id="header")
        yield Static(id="progress")
        yield OptionList(*PROVIDER_NAME_LIST, id="choice", compact=True)
        yield Input(id="prompt", compact=True)
        yield Static(id="note")
        yield Message()

    def on_mount(self) -> None:
        """Begin with the provider list as the only active widget."""

        self.query_one("#progress", Static).update(f"{CURRENT} Provider")
        self.query_one(Input).display = False
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Take the provider first; the same list later takes the SSH key choice."""

        if self.busy:
            return

        if self.step == SetupStep.PROVIDER:
            self._show_api_key_step(PROVIDER_NAME_LIST[event.option_index])
        elif event.option_index < len(self.public_key_path_list):
            self._submit(self.public_key_path_list[event.option_index])
        elif event.option_index == len(self.public_key_path_list):
            self._show_key_path_step()
        else:
            self._submit(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Validate the current field before advancing or registering a key."""

        if self.busy:
            return

        value = event.value.strip()
        if self.step == SetupStep.API_KEY and value:
            event.input.disabled = True
            self.check_api_key(value)
        elif self.step == SetupStep.DISK:
            self._accept_disk(value)
        elif self.step == SetupStep.KEY_PATH and value:
            self._submit(Path(value).expanduser())
        elif self.step == SetupStep.KEY_PATH:
            self._show_failure("Enter a public key file path.")

    def _show_api_key_step(self, provider_name: str) -> None:
        """Ask for the key beside the permissions it needs and where to create it."""

        self.provider_name = provider_name
        self.query_one("#progress", Static).update(f"{DONE} Provider   {provider_name}\n{CURRENT} API key")
        self.query_one("#note", Static).update(API_KEY_NOTE_MAP[provider_name])
        self._show_input(SetupStep.API_KEY)

    @work
    async def check_api_key(self, api_key: str) -> None:
        """Verify the key off the input loop; a refused key stays editable."""

        settings = self.app.settings.model_copy(
            update={"provider": self.provider_name, f"{self.provider_name}_api_key": SecretStr(api_key)}
        )
        try:
            await asyncio.to_thread(operations.check_api_key, settings)
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.confirmed_settings = settings
        self.api_key = api_key
        self.query_one(Input).disabled = False
        self._show_disk_step()

    def _show_disk_step(self) -> None:
        """Offer the default disk size for editing."""

        self.query_one("#progress", Static).update(
            f"{DONE} Provider   {self.provider_name}\n{DONE} API key    ••••\n{CURRENT} Disk (GB)"
        )
        self.query_one("#note", Static).update("")
        self._show_input(SetupStep.DISK, str(self.confirmed_settings.disk_gb))

    def _accept_disk(self, value: str) -> None:
        """Apply the shared settings validation before any SSH registration or config write."""

        try:
            self.confirmed_settings = Settings.model_validate(self.confirmed_settings.model_dump() | {"disk_gb": value})
        except ValidationError:
            self._show_failure("Enter a positive whole number of GB.")
            return

        self._show_ssh_key_step()

    def _show_ssh_key_step(self) -> None:
        """Offer discovered keys, a custom file path, or generation of a new pair."""

        self.step = SetupStep.SSH_KEY
        self.public_key_path_list = sorted((Path.home() / ".ssh").glob("*.pub"))
        self.query_one("#progress", Static).update(
            f"{DONE} Provider   {self.provider_name}\n{DONE} API key    ••••\n"
            f"{DONE} Disk       {self.confirmed_settings.disk_gb} GB\n{CURRENT} SSH key"
        )
        self.query_one("#note", Static).update("")
        self.query_one(Input).display = False

        choice = self.query_one(OptionList)
        choice.clear_options()
        choice.add_options([escape(f"~/.ssh/{path.name}") for path in self.public_key_path_list])
        choice.add_options(["Enter public key path…", "Generate new key"])
        choice.highlighted = 0
        choice.display = True
        choice.focus()

    def _show_key_path_step(self, value: str = "") -> None:
        """Ask for a public key file, starting from a path that is already known."""

        self.query_one("#note", Static).update("Public key file (.pub)")
        self._show_input(SetupStep.KEY_PATH, value)

    def _show_input(self, step: SetupStep, value: str = "") -> None:
        """Reuse one field for every typed answer, masking only the API key."""

        self.step = step
        self.query_one(OptionList).display = False

        prompt = self.query_one(Input)
        prompt.password = step == SetupStep.API_KEY
        prompt.value = value
        prompt.display = True
        prompt.focus()

    def _submit(self, public_key_path: Path | None) -> None:
        """Lock the screen and finish setup once; no path asks for a new key pair."""

        self.busy = True
        self.query_one(OptionList).disabled = True
        self.query_one(Input).disabled = True
        self.query_one(Message).show("Saving setup…")
        self.finish_setup(public_key_path)

    @work
    async def finish_setup(self, public_key_path: Path | None) -> None:
        """Register the key and save the configuration, then hand the settings to the app."""

        try:
            if public_key_path is None:
                public_key_path = await asyncio.to_thread(generate_key_pair, Path.home() / ".ssh" / "galloc_ed25519")
                # A refused registration then retries this key instead of generating another one.
                self._show_key_path_step(str(public_key_path))
            await asyncio.to_thread(
                operations.finish_setup, self.confirmed_settings, self.app.config_path, self.api_key, public_key_path
            )
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.dismiss(self.confirmed_settings.model_copy(update={"ssh_public_key": public_key_path}))

    def _show_failure(self, message: str) -> None:
        """Report the failure, unlock the screen, and let the current step be answered again."""

        self.busy = False
        self.query_one(Message).show(message, error=True)
        self.query_one(OptionList).disabled = False
        self.query_one(Input).disabled = False

        if self.step == SetupStep.SSH_KEY:
            self.query_one(OptionList).focus()
        else:
            self.query_one(Input).focus()

    def action_back(self) -> None:
        """Return from a custom path to the key choices; from any other step leave setup."""

        if self.step == SetupStep.KEY_PATH and not self.busy:
            self._show_ssh_key_step()
            return
        self.action_quit()

    def action_quit(self) -> None:
        """Leave without saving, unless a submission is still registering the key and writing the file."""

        if self.busy:
            self.query_one(Message).show("Finishing setup…")
            return
        self.app.exit()
